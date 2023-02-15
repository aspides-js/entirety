#!/usr/bin/python


# ------------------------------------
# python modules
# ------------------------------------

import sys, os, time
import shutil
import gzip as gz
from subprocess import check_output
import multiprocessing as mp
import matplotlib.pyplot as plt
import pandas as pd
import mmh3

import logging


# ------------------------------------
# own python modules
# ------------------------------------

from entirety.validate import assert_compressed

# ------------------------------------
# Main function
# ------------------------------------

def run2( options):
	print(options.region)
	filedir = ['/lustre/projects/Research_Project-MRC190311/ChIPSeq/simulate/chr1_1500000_normalgenome/2_binarised/10//chr1_region_binary.txt', '/lustre/projects/Research_Project-MRC190311/ChIPSeq/simulate/chr1_1500000_normalgenome/2_binarised/10//cell_chrY_binary.txt', '/lustre/projects/Research_Project-MRC190311/ChIPSeq/simulate/chr1_1500000_normalgenome/2_binarised/10//cell_chr1_binary.txt']
	print(region_limit(options.region, filedir))

def run( options ):
	# options
	metadir = options.metadir
	subdir = options.subdir
	bindir = options.bindir
	increment = options.increment

	cat_bed( options.files, options.subdir )
	total, nfile = wc( options.increment, options.subdir )

	start_time = time.time()
	print('Downsampling...')
	print("CPU number: "+str(mp.cpu_count()))
	pool = mp.Pool(processes=1)
	args = [(n, options, total) for n in range(1,nfile)] # nfile should be number calculated by wc()
	r = {} # initiate empty dictionary
	for res in pool.starmap(downsample, args):
		r.setdefault(res[0], [])
		r[res[0]].append(res[1])
	print("--- %s seconds ---" % (time.time() - start_time))
	
	args = [(n, options) for n in range(nfile)] # redefine args so the downsampled.0.bed is included

	print('Binarising...')
	r['0']=[total]
	for res in pool.starmap(binarise, args):
		r.setdefault(res[0], [])
		r[res[0]].append(res[1])
	print(r)
	print("--- %s seconds ---" % (time.time() - start_time))
	pool.close()

	param_write(r, options.outdir)
	param_plot(r, options.outdir)
	
	print('Complete')
	print(r)
	

#--------------------------------------------------------------------------------#

def cat_bed(files, subdir):
	'''
	Concatenate compressed or uncompressed files into downsampled.0.bed
	'''
	print("Concatenating files...")
	start_time = time.time()
	with open(subdir+"downsampled.0.bed",'wb') as wfd:
		for f in files:
			if assert_compressed(f):
				print('File is compressed')
				with gz.open(f,'rb') as fd:
					shutil.copyfileobj(fd, wfd)
			else:
				with open(f,'rb') as fd:
					shutil.copyfileobj(fd, wfd)
	print("--- %s seconds ---" % (time.time() - start_time))



def wc(increment, subdir):
	start_time = time.time()
	print("Calculating total read number...")
	total = int(check_output(["wc", "-l", subdir+"downsampled.0.bed"]).split()[0])/2
	print("--- %s seconds ---" % (time.time() - start_time))
	return total, int(total/increment)

#--------------------------------------------------------------------------------#

def params(proportion):
	'''
	Calculate a value between a range to reflect proportion of reads kept
	'''
	max_size = sys.maxsize
	min_size = -sys.maxsize - 1
	maxRange = max_size - min_size
	maxHashValue = min_size + round(maxRange * proportion)
	#return min_size, max_size, maxHashValue
	return maxHashValue


def discard(maxHashValue, seed, line):
	'''
	Generate a random hash from readname using seed. If number above proportional cut-off (True), discard read.
	'''
	readname=line.split('\t')[3].rsplit('/')[0] # extract readname, remove everything after '/' (read pair if paired)
	hashInt=mmh3.hash64(readname, seed)
	return hashInt[0] > maxHashValue


def downsample(n, options, total):
	''' 
	Downsample a bed file. For each read pair, a random value is assigned between the range. 
	The proportion is used to calculate a maximum acceptable value within the range. Records 
	whose value is below the limit are written to outfile, records whose hash value is above 
	the limit are discarded.

	Assumes a sorted paired bed file.
	todo: checks on sorted
	what to do with unpaired?
	'''
	proportion = (options.increment*n)/total
	outfile = options.subdir+'downsampled.'+str(n)+'.bed'
	reads = 0
	a=params(proportion)
	#print("proportion is")
	#print(a)
	with open(outfile, 'w') as outf:
		with open(options.subdir+"downsampled.0.bed",'r') as f:
			for line in f:
				if (hashDiscard(a, options.seed, line)):
					continue
				else:
					outf.write(line)
					reads+=1
	if options.paired:
		reads=reads/2
	return str(n), reads
	# which is the read number??

#--------------------------------------------------------------------------------#

def metafile_write(metadir, n):
	'''
	Creates the text file with specified input downsampled files for ChromHMM binarisation
	'''
	with open(metadir+"cellMarkBedFile."+n+".txt", "w") as outf:
		outf.write("cell\tmark\tdownsampled."+n+".bed")  

def region_limit(region, filedir):
	chrom = region.split(':')[0]
	file = [i for i in filedir if "cell_"+chrom in i][0]
	number = region.split(':')[1].split('-')
	number = [int(int(n)/200)-1 for n in number]

	chr_file=os.path.dirname(file)+"/"+chrom+"_region_binary.txt"
	with open(chr_file, "w") as outf:
		with open(file, 'r') as f:
			outf.write(next(f))
			outf.write(next(f))
			check = f.readlines()
		check = ''.join(check[number[0]:number[1]])
		outf.write(check)
	filedir = [chr_file]
	return filedir

def count_mark(ndir, n, bindir, region):
	filedir=os.listdir(bindir+ndir)
	filedir=[bindir+ndir+'/'+file for file in filedir]
	#filedir=['cell_chr1_binary.txt']
	if region != '':
		print(region)
		filedir = region_limit(region, filedir)
		
	count=0
	total=0
	for file in filedir:
		with open(file, 'r') as f:
			next(f)
			next(f) #discard first two lines
			for l in f:
				if l == "1\n":
					count+=1
					total+=1
				else:
					total+=1
	return n, count/total


def binarise(n, options):
	n = str(n)
	metafile_write(options.metadir, str(n))
	if (len(n) < 2):
		ndir = "0"+n
	else:
		ndir = str(n)

	if not os.path.exists(options.bindir+ndir):
		os.mkdir(options.bindir+ndir)

	os.system("java -mx2400M -jar "+options.chromhmmJar+" BinarizeBed -b 200 "+options.genome+" "+options.subdir+" "+options.metadir+"cellMarkBedFile."+n+".txt "+options.bindir+ndir)
	res = count_mark(ndir, n, options.bindir, options.region)
	return res


#--------------------------------------------------------------------------------#

def param_write(r, outdir):
	with open(outdir+"/completeness.txt", 'w') as f:
		for key, value in r.items():
			f.write('%s\t%s\t%s\n' % (key, value[0], value[1]))


def param_plot(r, outdir):
	df = pd.DataFrame(r)
	plt.figure(figsize=(10,6), tight_layout=True)
	plt.plot(df.loc[1].tolist(), df.loc[2].tolist(), 'gs-')
	plt.xlabel('Number of Reads')
	plt.ylabel('Proportion of marks')
	plt.savefig(outdir+'/completeplot.jpg')


#-------------------------------------------------------------------------------#

def v(s, Vm, Km):
    return (Vm * s) / (Km + s)

def residuals(p, data[0], data[1]):
	Vm = p['Vm']
	Km = p['Km']
	fi = v(data[0], Vm, Km)
	return data[1] - fi

params = lmfit.Parameters()
params.add('Vm', value = 1, min=0, max=1000000000)
params.add('Km', value = 1, min=0, max=1000000000)

result = lmfit.minimize(residuals, params, args(data[0], data[1]))