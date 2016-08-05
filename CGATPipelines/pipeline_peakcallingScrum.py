"""
pipeline_peakcalling.py - Window based genomic analysis
===================================================

:Author: Andreas Heger
:Release: $Id$
:Date: |today|
:Tags: Python


Methods
=======

Usage
=====

See :ref:`PipelineSettingUp` and :ref:`PipelineRunning` on general
information how to use CGAT pipelines.

Configuration
-------------

Input
-----


Pipeline output
===============


Code
====

"""

# load modules
from ruffus import *
from ruffus.combinatorics import *
import sys
import os
import re
import csv
import numpy
import sqlite3
import pandas
import glob
import CGAT.Experiment as E
import CGAT.IOTools as IOTools
import CGATPipelines.Pipeline as P
import CGATPipelines.PipelinePeakcallingScrum as PipelinePeakcalling
import CGAT.BamTools as Bamtools

import CGAT.BamTools as BamTools
import pandas as pd

#########################################################################
#########################################################################
#########################################################################
# load options from the config file
P.getParameters(
    ["%s/pipeline.ini" % os.path.splitext(__file__)[0],
     "../pipeline.ini",
     "pipeline.ini"],
    defaults={
        'paired_end': False})

PARAMS = P.PARAMS

PARAMS.update(P.peekParameters(
    PARAMS["annotations_dir"],
    "pipeline_annotations.py",
    prefix="annotations_",
    update_interface=True))

df = pd.read_csv("design.tsv", sep="\t")

INPUTBAMS = list(set(df['bamControl'].values))

CHIPBAMS = list(set(df['bamReads'].values))

pairs = zip(df['bamReads'], df['bamControl'])


if PARAMS['IDR_poolinputs'] == "none":
    inputD = dict(pairs)
    conditions = df['Condition'].values
    tissues = df['Tissue'].values
    i = 0
    for C in CHIPBAMS:
        cond = conditions[i]
        tissue = tissues[i]
        inputD["%s_%s.bam" % (cond, tissue)] = "%s_%s_pooled.bam" % (
            cond, tissue)
        i += 1


elif PARAMS['IDR_poolinputs'] == "all":
    inputD = dict()
    for C in CHIPBAMS:
        inputD[C] = "pooled_all.bam"

elif PARAMS['IDR_poolinputs'] == "condition":
    inputD = dict()
    conditions = df['Condition'].values
    tissues = df['Tissue'].values
    i = 0
    for C in CHIPBAMS:
        cond = conditions[i]
        tissue = tissues[i]
        inputD[C] = "%s_%s_pooled.bam" % (cond, tissue)
        inputD["%s_%s.bam" % (cond, tissue)] = "%s_%s_pooled.bam" % (
            cond, tissue)
        i += 1

print inputD

if Bamtools.isPaired(CHIPBAMS[0]) is True:
    PARAMS['paired_end'] = True

###################################################################
# Helper functions mapping tracks to conditions, etc
###################################################################
# load all tracks - exclude input/control tracks


def readTable(tabfile):
    df = pd.read_csv(tabfile, sep="\t")

    chips = df['ChipBam'].values
    inputs = df['InputBam'].values
    pairs = zip(chips, inputs)
    D = dict(pairs)
    return D


def connect():
    '''connect to database.

    This method also attaches to helper databases.
    '''

    dbh = sqlite3.connect(PARAMS["database_name"])
    statement = '''ATTACH DATABASE '%s' as annotations''' %\
                (PARAMS["annotations_database"])
    cc = dbh.cursor()
    cc.execute(statement)
    cc.close()

    return dbh

###############################################################
# Preprocessing Steps


@follows(mkdir("filtered_bams.dir"))
@transform(INPUTBAMS, regex("(.*).bam"),
           [r"filtered_bams.dir/\1_filtered.bam",
            r"filtered_bams.dir/\1_counts.tsv"])
def filterInputBAMs(infile, outfiles):
    '''
    Applies various filters specified in the pipeline.ini to the bam file
    Currently implemented are filtering unmapped, unpaired and duplicate
    reads and filtering reads overlapping with blacklisted regions
    based on a list of bam files.
    '''
    filters = PARAMS['filters_bamfilters'].split(",")
    bedfiles = PARAMS['filters_bedfiles'].split(",")
    blthresh = PARAMS['filters_blacklistthresh']
    PipelinePeakcalling.filterBams(infile, outfiles, filters, bedfiles,
                                   float(blthresh),
                                   PARAMS['paired_end'],
                                   PARAMS['filters_strip'],
                                   PARAMS['filters_keepint'])


@follows(mkdir("filtered_bams.dir"))
@transform(CHIPBAMS, regex("(.*).bam"), [r"filtered_bams.dir/\1_filtered.bam",
                                         r"filtered_bams.dir/\1_counts.tsv"])
def filterChipBAMs(infile, outfiles):
    '''
    Applies various filters specified in the pipeline.ini to the bam file
    Currently implemented are filtering unmapped, unpaired and duplicate
    reads and filtering reads overlapping with blacklisted regions
    based on a list of bam files.
    '''
    filters = PARAMS['filters_bamfilters'].split(",")
    bedfiles = PARAMS['filters_bedfiles'].split(",")
    blthresh = PARAMS['filters_blacklistthresh']
    PipelinePeakcalling.filterBams(infile, outfiles, filters, bedfiles,
                                   float(blthresh),
                                   PARAMS['paired_end'],
                                   PARAMS['filters_strip'],
                                   PARAMS['filters_keepint'])


if int(PARAMS['IDR_run']) == 1:
    @follows(mkdir("pooled_bams.dir"))
    @split(filterChipBAMs,
           r"pooled_bams.dir/*_pooled_filtered.bam")
    def makePooledBams(infiles, outfiles):
        '''
        IDR requires one bam file for each replicate and a pooled bam
        file of all replicates for a particular condition and tissue.
        This function generates the pooled bam files.
        '''
        infile = infiles[0]
        cond_tissues = set(df['Condition'] + "_" + df['Tissue'])

        for ct in cond_tissues:
            p = ct.split("_")
            cond = p[0]
            tissue = p[1].split(".")[0]
            subdf = df[((df['Condition'] == cond) & (df['Tissue'] == tissue))]
            innames = subdf['bamReads'].values
            innames = set(
                ["filtered_bams.dir/%s" % s.replace(".bam", "_filtered.bam")
                 for s in innames])

            out = "pooled_bams.dir/%s_pooled_filtered.bam" % ct

            infiles = " ".join(innames)

            T1 = P.getTempFilename(".")
            T2 = P.getTempFilename(".")
            statement = """samtools merge %(T1)s.bam  %(infiles)s;
            samtools sort %(T1)s.bam -o %(T2)s.bam;
            samtools index %(T2)s.bam;
            mv %(T2)s.bam %(out)s;
            mv %(T2)s.bam.bai %(out)s.bai""" % locals()
            P.run()
            os.remove("%s.bam" % T1)
            os.remove(T1)

    @active_if(PARAMS['IDR_poolinputs'] != "all")
    @follows(mkdir('IDR_inputs.dir'))
    @split(filterInputBAMs, "IDR_inputs.dir/*_pooled_filtered.bam")
    def makePooledInputs(infiles, outfiles):
        '''
        As pooled BAM files are used in the IDR, pooled input files also
        need to be generated - combined bam files of all the input bam
        files for this tissue.
        If you have chosen the "all" option for IDR_poolinputs in the
        pipeline.ini, this step is skipped, as all inputs are pooled for
        all IDR analyses.
        '''
        cond_tissues = set(df['Condition'] + "_" + df['Tissue'])
        for ct in cond_tissues:
            p = ct.split("_")
            cond = p[0]
            tissue = p[1].split(".")[0]
            subdf = df[((df['Condition'] == cond) & (df['Tissue'] == tissue))]
            inputs = subdf['bamControl'].values
            inputs = set(
                ["filtered_bams.dir/%s" % s.replace(".bam", "_filtered.bam")
                 for s in inputs])

            out = "IDR_inputs.dir/%s_pooled_filtered.bam" % ct

            infiles = " ".join(inputs)

            T1 = P.getTempFilename(".")
            T2 = P.getTempFilename(".")
            statement = """samtools merge %(T1)s.bam  %(infiles)s;
            samtools sort %(T1)s.bam -o %(T2)s.bam;
            samtools index %(T2)s.bam;
            mv %(T2)s.bam %(out)s;
            mv %(T2)s.bam.bai %(out)s.bai""" % locals()
            P.run()
            os.remove("%s.bam" % T1)
            os.remove(T1)

else:
    @transform(filterChipBAMs, regex("filtered_bams.dir/(.*).bam"),
               r'filtered_bams.dir/\1.bam')
    def makePooledBams(infile, outfile):
        '''
        Dummy task if IDR not requested.
        '''
        pass


if int(PARAMS['IDR_run']) == 1:
    @follows(mkdir("peakcalling_bams.dir"))
    @subdivide((filterChipBAMs, makePooledBams),
               regex("(.*)_bams.dir/(.*).bam"),
               [r"peakcalling_bams.dir/\2_pseudo_1.bam",
                r"peakcalling_bams.dir/\2_pseudo_2.bam",
                r"peakcalling_bams.dir/\2.bam"])
    def makePseudoBams(infiles, outfiles):
        '''
        Generates pseudo bam files each containing approximately 50% of reads
        from the original bam file for IDR analysis.
        Also generates a link to the original BAM file in the
        peakcalling_bams.dir directory.

        '''
        # makePooledBams generates a single output whereas filterChipBAMS
        # generates a bam file and a table - a list of outputs
        if isinstance(infiles, list):
            infile = infiles[0]
        else:
            infile = infiles

        pseudos = outfiles[0:2]
        orig = outfiles[2]

        cwd = os.getcwd()

        os.system("""
        ln -s %(cwd)s/%(infile)s %(cwd)s/%(orig)s;
        ln -s %(cwd)s/%(infile)s.bai %(cwd)s/%(orig)s.bai;
        """ % locals())

        PipelinePeakcalling.makePseudoBams(infile, pseudos,
                                           PARAMS['paired_end'],
                                           PARAMS['IDR_randomseed'],
                                           submit=True)
else:
    @transform(filterChipBAMs, regex("filtered_bams.dir/(.*)_filtered.bam"),
               r'peakcalling_bams.dir/\1.bam')
    def makePseudoBams(infile, outfile):
        '''
        Link to original BAMs without generating pseudo bams
        if IDR not requested.
        '''
        cwd = os.getcwd()
        os.system("""
        ln -s %(cwd)s/%(infile)s %(cwd)s/%(outfile)s;
        ln -s %(cwd)s/%(infile)s.bai %(cwd)s/%(outfile)s.bai;
        """ % locals())
        P.run()


'''
IDR_poolinputs has three possible options:

none
Use the input files per replicate as specified in the design file
Input files will still also be pooled if IDR is specified as IDR requires BAM
files representing pooled replicates as well as BAM files for each replicate

all
Pool all input files and use this single pooled input BAM file as the input
for any peakcalling.  Used when input is low depth or when only a single
input or replicates of a single input are available.

condition
pool the input file for the

'''
if PARAMS['IDR_poolinputs'] == "none":
    @follows(mkdir('IDR_inputs.dir'))
    @transform(filterInputBAMs, regex("filtered_bams.dir/(.*).bam"),
               r'IDR_inputs.dir/\1.bam')
    def makeIDRInputBams(infile, outfile):
        '''
        '''
        infile = infile[0]
        cwd = os.getcwd()
        os.system("""
        ln -s %(cwd)s/%(infile)s %(cwd)s/%(outfile)s;
        ln -s %(cwd)s/%(infile)s.bai %(cwd)s/%(outfile)s.bai;
        """ % locals())


elif PARAMS['IDR_poolinputs'] == "all":
    @follows(mkdir('IDR_inputs.dir'))
    @merge(filterInputBAMs, "IDR_inputs.dir/pooled_all.bam")
    def makeIDRInputBams(infiles, outfile):
        infiles = [i[0] for i in infiles]
        infiles = " ".join(infiles)
        T1 = P.getTempFilename(".")
        T2 = P.getTempFilename(".")
        statement = """samtools merge %(T1)s.bam  %(infiles)s;
        samtools sort %(T1)s.bam -o %(T2)s.bam;
        samtools index %(T2)s.bam;
        mv %(T2)s.bam %(outfile)s;
        mv %(T2)s.bam.bai %(outfile)s.bai""" % locals()
        P.run()
        os.remove("%s.bam" % T1)
        os.remove(T1)


elif PARAMS['IDR_poolinputs'] == "condition" and PARAMS['IDR_run'] != 1:
    @follows(mkdir('IDR_inputs.dir'))
    @split(filterInputBAMs, r'IDR_inputs.dir/*.bam')
    def makeIDRInputBams(infiles, outfiles):
        outs = set(inputD.values())
        for out in outs:
            p = out.split("_")
            cond = p[1]
            tissue = p[2].split(".")[0]
            subdf = df[((df['Condition'] == cond) & (df['Tissue'] == tissue))]
            innames = subdf['bamControl'].values
            innames = set(
                ["filtered_bams.dir/%s" % s.replace(".bam", "_filtered.bam")
                 for s in innames])
            out = "IDR_inputs.dir/%s" % out
            infiles = " ".join(innames)
            outfile = out

            T1 = P.getTempFilename(".")
            T2 = P.getTempFilename(".")
            statement = """samtools merge %(T1)s.bam  %(infiles)s;
            samtools sort %(T1)s.bam -o %(T2)s.bam;
            samtools index %(T2)s.bam;
            mv %(T2)s.bam %(outfile)s;
            mv %(T2)s.bam.bai %(outfile)s.bai""" % locals()
            P.run()
            os.remove("%s.bam" % T1)
            os.remove(T1)


elif PARAMS['IDR_poolinputs'] == "condition" and PARAMS['IDR_run'] == 1:
    @follows(mkdir('IDR_inputs.dir'))
    @follows(mkdir('IDR_inputs.dir'))
    @transform(makePooledInputs, regex("IDR_inputs.dir/(.*).bam"),
               r'IDR_inputs.dir/\1.bam')
    def makeIDRInputBams(infiles, outfiles):
        pass


@follows(makeIDRInputBams)
@follows(filterInputBAMs)
@follows(makePooledBams)
@follows(makePooledInputs)
@follows(makePseudoBams)
@originate("peakcalling_bams_and_inputs.tsv")
def makeBamInputTable(outfile):
    '''
    Generates a tab delimited file - peakcalling_bams_and_inputs.tsv
    which links each filtered bam file in the peakcalling_bams.dir
    directory to the appropriate input in the IDR_inputs.dir
    directory.
    Uses the dictionary inputD generated as a global variable based
    on the user-specified design table plus pooled input files generated
    above.
    '''
    ks = inputD.keys()
    out = IOTools.openFile(outfile, "w")
    out.write('ChipBam\tInputBam\n')
    bamfiles = os.listdir("peakcalling_bams.dir")

    for k in ks:
        inputstem = inputD[k]
        chipstem = k
        chipstem = P.snip(chipstem)
        inputstem = P.snip(inputstem)
        inputfile = "IDR_inputs.dir/%s_filtered.bam" % inputstem

        for b in bamfiles:
            if b.startswith(chipstem) and b.endswith('bam'):
                out.write("peakcalling_bams.dir/%s\t%s\n" % (b, inputfile))
    out.close()


@transform(makePseudoBams, suffix(".bam"), "_insertsize.tsv")
def estimateInsertSize(infile, outfile):
    '''
    Predicts insert size using MACS2 for single end data and using Bamtools
    for paired end data.
    Output is stored in insert_size.tsv
    '''
    PipelinePeakcalling.estimateInsertSize(infile, outfile,
                                           PARAMS['paired_end'],
                                           PARAMS['insert_alignments'],
                                           PARAMS['insert_macs2opts'])


@merge(estimateInsertSize, "insert_sizes.tsv")
def mergeInsertSizes(infiles, outfile):
    '''
    Combines insert size outputs into one file
    '''
    out = IOTools.openFile(outfile, "w")
    out.write("filename\tmode\tfragmentsize_mean\tfragmentsize_std\ttagsize\n")
    for infile in infiles:
        res = IOTools.openFile(infile).readlines()
        out.write("%s\t%s\n" % (infile, res[-1].strip()))
    out.close()


@follows(makeBamInputTable)
@follows(mergeInsertSizes)
@transform(makePseudoBams, regex("(.*)_bams\.dir\/(.*)\.bam"),
           r"\1_bams.dir/\2.bam")
def preprocessing(infile, outfile):
    '''
    Dummy task to ensure all preprocessing has run and
    bam files are passed individually to the next stage.
    '''
    pass

# ###############################################################
#  Peakcalling Steps


@follows(mkdir('macs2.dir'))
@transform(preprocessing,
           regex("peakcalling_bams.dir/(.*).bam"),
           add_inputs(makeBamInputTable),
           r"macs2.dir/\1.macs2")
def callMacs2peaks(infiles, outfile):
    D = readTable(infiles[1])
    bam = infiles[0]
    inputf = D[bam]
    insertsizef = "%s_insertsize.tsv" % (P.snip(bam))

    peakcaller = PipelinePeakcalling.Macs2Peakcaller(
        threads=1,
        paired_end=True,
        output_all=True,
        tool_options=PARAMS['macs2_options'],
        tagsize=None)

    statement = peakcaller.build(bam, outfile, PARAMS['macs2_contigsfile'],
                                 inputf, insertsizef)
    P.run()


PEAKCALLERS = []
mapToPeakCallers = {'macs2': (callMacs2peaks,)}

for x in P.asList(PARAMS['peakcalling_peakcallers']):
    PEAKCALLERS.extend(mapToPeakCallers[x])


@follows(*PEAKCALLERS)
def peakcalling():
    '''
    dummy task to define upstream peakcalling tasks
    '''

'''
################################################################
# IDR Steps
@follows(peakcalling)
@transform(peakcalling,
def preprocessForIDR(infile, outfile):
'''


################################################################
# QC Steps


################################################################
# Fragment GC% distribution
################################################################

"""
@follows(mkdir("QC.dir"))
@transform(BAMS, regex("(.*).bam"), r"QC.dir/\1.tsv")
def fragLenDist(infile, outfile):

    if PARAMS["paired_end"] == 1:
        function = "--merge-pairs"
    else:
        function = "--fragment"

    genome = os.path.join(PARAMS["general_genome_dir"],
                          PARAMS["general_genome"])
    genome = genome + ".fasta"

    statement = '''
    samtools view -s 0.2 -ub %(infile)s |
    python %(scriptsdir)s/bam2bed.py  %(function)s |
    python %(scriptsdir)s/bed2table.py --counter=composition-na -g %(genome)s\
    > %(outfile)s
    '''
    P.run()


@merge(BAMS, regex("(.*).bam"), r"QC.dir/genomic_coverage.tsv")
def buildReferenceNAComposition(infiles, outfile):

    infile = infiles[0]
    contig_sizes = os.path.join(PARAMS["annotations_dir"],
                                PARAMS["annotations_interface_contigs"])
    gaps_bed = os.path.join(PARAMS["annotations_dir"].
                            PARAMS["annotations_interface_gaps_bed"])

    statement = '''bedtools shuffle
    -i %(infile)s
    -g %(contig_sizes)s
    -excl %(gaps_bed)s
    -chromFirst
    | python %(scriptsdir)s/bed2table.py
    --counter=composition-na
    -g %(genome)s > %(outfile)s
    '''

    P.run()
################################################################
"""


def full():
    pass


@follows(mkdir("report"))
def build_report():
    '''build report from scratch.'''

    E.info("starting documentation build process from scratch")
    P.run_report(clean=True)


@follows(mkdir("report"))
def update_report():
    '''update report.'''

    E.info("updating documentation")
    P.run_report(clean=False)


@follows(mkdir("%s/bamfiles" % PARAMS["web_dir"]),
         mkdir("%s/medips" % PARAMS["web_dir"]),
         )
def publish():
    '''publish files.'''

    # directory : files

    # publish web pages
    P.publish_report(export_files=export_files)

if __name__ == "__main__":
    sys.exit(P.main(sys.argv))