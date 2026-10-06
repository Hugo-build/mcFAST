#!/bin/bash
#SBATCH --job-name=mcfast-study
#SBATCH --nodes=10
#SBATCH --ntasks-per-node=8
#SBATCH --cpus-per-task=1
#SBATCH --time=96:00:00
#SBATCH --mem-per-cpu=4G
#SBATCH --output=mcfast-study-%j.out
#SBATCH --error=mcfast-study-%j.err

# Override resources, account, and partition on the sbatch command line.
# Example: sbatch --account=ACCOUNT --partition=PARTITION --nodes=10 \
#   --ntasks-per-node=8 scripts/mcfast-study.slurm /shared/path/to/snapshot
# Prepare the snapshot on the cluster's shared filesystem before submitting.
# Load OpenFAST/TurbSim modules and activate the Linux mcFAST environment before
# sbatch, or add your site's module/activation commands below.
set -euo pipefail

snapshot=${1:?Usage: sbatch scripts/mcfast-study.slurm SNAPSHOT_PATH}
python_bin=${MCFAST_PYTHON:-python}
node_count=${SLURM_JOB_NUM_NODES:?Run this script using sbatch}
workers_per_node=${SLURM_NTASKS_PER_NODE:?Set --ntasks-per-node to the number of simultaneous cases per node}
if ! [[ "$workers_per_node" =~ ^[1-9][0-9]*$ ]]; then
  echo "Use a single positive integer for --ntasks-per-node" >&2
  exit 2
fi
worker_count=$((node_count * workers_per_node))

# Native solvers run one thread per case, so each CPU runs an independent case.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 OMP_DYNAMIC=FALSE

"$python_bin" -m mcfast.slurm status --snapshot "$snapshot"
echo "Starting $worker_count case workers across $node_count nodes ($workers_per_node per node)"
# Rank r processes cases r, r + worker_count, ... . Every case has one owner.
# Keep other workers running when one finishes with failed cases.
set +e
srun --nodes="$node_count" --ntasks="$worker_count" \
  --ntasks-per-node="$workers_per_node" --cpus-per-task=1 \
  --cpu-bind=cores --mpi=none --kill-on-bad-exit=0 --wait=0 \
  "$python_bin" -m mcfast.slurm worker --snapshot "$snapshot"
run_code=$?
set -e
"$python_bin" -m mcfast.slurm status --snapshot "$snapshot"
exit "$run_code"
