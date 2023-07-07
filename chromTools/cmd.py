#!/usr/bin/python


# ------------------------------------
# modules
# ------------------------------------

import gzip as gz
import multiprocessing as mp
import pathlib
import shutil
import sys
import tempfile
import time
from subprocess import check_output

import lmfit
import matplotlib.pyplot as plt
import mmh3
import numpy as np
import pandas as pd

from chromTools.chmm_cmd import make_binary_data_from_bed, make_gridcontrol
from chromTools.validate import assert_compressed, chmm_validator, macs_validator


# ------------------------------------
# Main function
# ------------------------------------
def run(options):
    """
    The main function pipeline for chromTools.

    :param options: Command line options.
    :type options: Namespace object.
    """
    ## Concatenating
    cat_bed(options.files, options.control, options.subdir, options.info)
    total, nfile = wc(
        options.increment, options.subdir, options.info, options.warn, options.paired
    )

    start_time = time.time()
    options.start_time = start_time

    if options.control:
        options.info("Calculating control grid...")
        gridcontrol, sumgridcontrol, bpresentcontrol = chmm_gridcontrol(options)
    else:
        gridcontrol, sumgridcontrol, bpresentcontrol = None, None, None
    options.info(f"--- {(time.time() - start_time)} seconds ---")

    ## Downsampling
    options.info("Downsampling...")
    options.info(f"CPU number: {str(mp.cpu_count())}")

    pool = mp.Pool()
    args = [
        (n, options, total) for n in range(1, nfile)
    ]  # nfile should be number calculated by wc()

    r = {}  # initiate empty dictionary
    for res in pool.starmap(downsample, args):
        r.setdefault(res[0], [])
        r[res[0]].append(res[1])
    options.info(f"--- {(time.time() - start_time)} seconds ---")

    # nfile = 1

    ## Binarising
    options.info("Binarising...")
    args = [
        (n, gridcontrol, sumgridcontrol, bpresentcontrol, options)
        for n in range(0, nfile)
    ]  # nfile should be number calculated by wc()
    print(time.time() - start_time)
    r["0"] = [total]
    print(f"args len is: {len(args)}")
    for res in pool.starmap(use_chmm, args):
        r.setdefault(res[0], [])
        r[res[0]].append(res[1])
    options.info(f"--- {(time.time() - start_time)} seconds ---")

    param_write(r, options.outdir)
    param_plot(r, options.outdir)


# --------------------------------------------------------------------------------#


def cat_bed(files, control, subdir, info):
    """
    Concatenate compressed or uncompressed files into downsampled.0.bed.

    :param files: Files to concatenate.
    :type files: str
    :param control: Control files to concatenate.
    :type control: str or None
    :param subdir: Path to the subsample directory.
    :type subdir: str
    :param info: Logging function for informational messages.

    """
    info("Concatenating files...")
    start_time = time.time()
    with open(pathlib.Path(subdir / "downsampled.0.bed"), "wb") as wfd:
        for f in files:
            if assert_compressed(f):
                print("File is compressed")
                with gz.open(f, "rb") as fd:
                    shutil.copyfileobj(fd, wfd)
            else:
                with open(f, "rb") as fd:
                    shutil.copyfileobj(fd, wfd)
    if control != False:
        with open(pathlib.Path(subdir / "downsampled.ctrl.bed"), "wb") as wfd:
            for f in control:
                if assert_compressed(f):
                    print("File is compressed")
                    with gz.open(f, "rb") as fd:
                        shutil.copyfileobj(fd, wfd)
                else:
                    with open(f, "rb") as fd:
                        shutil.copyfileobj(fd, wfd)

    info(f"--- {(time.time() - start_time)} seconds ---")


def wc(increment, subdir, info, warn, paired):
    """Count total read number

    :param increment: The amount to increase each subsampled file by.
    :type increment: int
    :param subdir: Path to the subsample directory.
    :type subdir: str
    :param info: Logging function for informational messages.
    :param warn: Logging function for warning messages.
    :param paired: Indicates if the reads are paired end.
    :type paired: bool

    :return: A tuple containing the total number of reads and the number of files that will be generated.
    :rtype: tuple[int, int]

    """
    start_time = time.time()
    info("Calculating total read number...")
    total = int(check_output(["wc", "-l", f"{subdir}/downsampled.0.bed"]).split()[0])

    if paired:
        total = total / 2

    info(f"--- {(time.time() - start_time)} seconds ---")

    nfile = int(total / increment)

    # give warning if nfile is very high
    if nfile > 100:
        warn(f"Number of downsampled files will be {nfile}")

    if total == 0:
        warn(
            "Total number of lines is equal to 0. Are your input files empty? Terminating."
        )
        sys.exit(1)

    if total < increment:
        warn(
                f"Increment is larger than whole dataset read number. Terminating."
            )
        sys.exit(1)

    return total, nfile


# --------------------------------------------------------------------------------#


def params(proportion):
    """
    Calculate a value within a range to reflect the proportion of reads kept.

    :param proportion: Proportion of reads to subsample from the whole dataset.
    :type proportion: int
    :return: Maximum hash value threshold above which reads are discarded.
    :rtype: int
    """
    max_size = sys.maxsize
    min_size = -sys.maxsize - 1
    maxRange = max_size - min_size
    maxHashValue = min_size + round(maxRange * proportion)
    return maxHashValue


def discard(maxHashValue, seed, line):
    """Generate a random hash from readname using seed. If number above proportional cut-off (True), discard read.

    Args:
            maxHashValue (int): Threshold above which reads are discarded
            seed (int): Random seed
            line (str): Read/line in file

    Returns:
            bool: Boolean specifying if readname is below or above discard threshold
    """
    readname = line.split("\t")[3].rsplit("/")[
        0
    ]  # extract readname, remove everything after '/' (read pair if paired)
    hashInt = mmh3.hash64(readname, seed)
    return hashInt[0] > maxHashValue


def downsample(n, options, total):
    """Downsample a bed file. For each read pair, a random value is assigned between the range.
    The proportion is used to calculate a maximum acceptable value within the range. Records
    whose value is below the limit are written to outfile, records whose hash value is above
    the limit are discarded.

    :param n: Numerical descriptor of file
    :type n: int
    :param total: Total number of reads/read pairs in downsampled.0.bed
    :type total: int
    :param options: Command line arguments
    :type options: Namespace object

    :return: A tuple containing the numerical descriptor of file and the number of reads.
    :rtype: tuple[str, float]

    """
    proportion = (options.increment * n) / total
    outfile = pathlib.Path(options.subdir / f"downsampled.{n}.bed")
    reads = 0
    a = params(proportion)
    with open(outfile, "w") as outf:
        with open(pathlib.Path(options.subdir / "downsampled.0.bed"), "r") as f:
            for line in f:
                if discard(a, options.seed, line):
                    continue
                else:
                    outf.write(line)
                    reads += 1
    if options.paired:
        reads = reads / 2
    return str(n), reads


# --------------------------------------------------------------------------------#

def use_chmm(n, gridcontrol, sumgridcontrol, bpresentcontrol, options):
    options = chmm_validator(options)
    count, total = make_binary_data_from_bed(
        n, gridcontrol, sumgridcontrol, bpresentcontrol, options
    )
    return str(n), count / total


def chmm_gridcontrol(options):
    options = chmm_validator(options)
    gridcontrol, sumgridcontrol, bpresentcontrol = make_gridcontrol(options)
    return gridcontrol, sumgridcontrol, bpresentcontrol


# --------------------------------------------------------------------------------#


def param_write(r, outdir):
    """
    Write the output to a text file in TSV format.

    :param r: A dictionary of subsampled files and corresponding read and proportion values.
    :type r: dict
    :param outdir: The output directory.
    :type outdir: str
    :return: None
    :rtype: None
    """
    with open(pathlib.Path(outdir / "completeness.txt"), "w") as f:
        for key, value in r.items():
            f.write(f"{key}\t{value[0]}\t{value[1]}\n")


def param_plot(r, outdir):
    """
    Plot the output graph and call the Michaelis-Menten function.

    :param r: A dictionary of subsampled files and corresponding read and proportion values.
    :type r: dict
    :param outdir: The output directory.
    :type outdir: str
    :return: None
    :rtype: None
    """
    df = pd.DataFrame(r)
    plt.figure(figsize=(10, 6), tight_layout=True)
    plt.plot(df.loc[0].tolist(), df.loc[1].tolist(), "s-", color="#06846a")
    plt.xlabel("Number of Reads")
    plt.ylabel("Proportion of marks")
    plt.savefig(pathlib.Path(outdir / "completeplot.jpg"))

    mm(df, outdir)


# -------------------------------------------------------------------------------#


def v(s, Vm, Km):
    """
    Calculate the reaction rate based on the substrate concentration.

    :param s: The substrate concentration.
    :type s: float
    :param Vm: The maximum reaction rate.
    :type Vm: float
    :param Km: The Michaelis-Menten constant.
    :type Km: float
    :return: The reaction rate.
    :rtype: float
    """
    return (Vm * s) / (Km + s)


def residuals(p, x, y):
    """
    Calculate the residuals between observed and predicted values by subtracting the predicted values (fi) from the observed values (y).

    :param p: The parameter values.
    :type p: dict
    :param x: The independent variable values.
    :type x: array-like
    :param y: The observed values.
    :type y: array-like
    :return: The residuals.
    :rtype: array-like
    """
    Vm = p["Vm"]
    Km = p["Km"]
    fi = v(x, Vm, Km)
    return y - fi


def mm(df, outdir):
    """
    Perform Michaelis-Menten analysis on the given data.

    :param df: The input data as a DataFrame with two columns.
    :type df: pandas.DataFrame
    :param outdir: The directory path for saving the output files.
    :type outdir: str
    :return: None
    :rtype: None
    """
    data = np.array(df)

    params = lmfit.Parameters()
    params.add("Vm", value=1, min=0, max=5_000_000_000)
    params.add("Km", value=1, min=0, max=5_000_000_000)

    result = lmfit.minimize(residuals, params, args=(data[0], data[1]))

    fm = np.linspace(0, max(data[0]), 100)
    plt.figure(figsize=(10, 6), tight_layout=True)
    plt.scatter(df.loc[0].tolist(), df.loc[1].tolist(), color="k")
    plt.plot(fm, v(fm, result.params["Vm"].value, result.params["Km"].value), "k")
    plt.xlabel("[S] (reads)")
    plt.ylabel("v (proportion)")
    plt.axhline(y=result.params["Vm"].value, linestyle="-", color="#06846a")
    if result.params["Km"].value < max(data[0]):
        plt.axvline(x=result.params["Km"].value, linestyle="-", color="#06846a")

    plt.title(label=f'Vm: {result.params["Vm"].value}')
    plt.savefig(pathlib.Path(outdir / "mmplot.jpg"))

    with open(pathlib.Path(outdir / "mm.txt"), "w") as f:
        f.write(f'{result.params["Vm"].value}\t{result.params["Km"].value}')


# -------------------------------------------------------------------------------#
