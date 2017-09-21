from __future__ import print_function
import os
import datetime
import subprocess
from future.utils import raise_with_traceback
from datetime import timedelta
from pathlib import Path
import numpy as np
import glob
from contextlib import contextmanager
import time
from collections import defaultdict
import progressbar

from spectral_dagger.utils.misc import make_symlink

from dps.parallel.base import ReadOnlyJob, zip_root
from dps.utils import parse_date


def make_directory_name(experiments_dir, network_name, add_date=True):
    if add_date:
        working_dir = os.path.join(experiments_dir, network_name + "_")
        dts = str(datetime.datetime.now()).split('.')[0]
        for c in [":", " ", "-"]:
            dts = dts.replace(c, "_")
        working_dir += dts
    else:
        working_dir = os.path.join(experiments_dir, network_name)

    return working_dir


def parse_timedelta(s):
    """ ``s`` should be of the form HH:MM:SS """
    args = [int(i) for i in s.split(":")]
    return timedelta(hours=args[0], minutes=args[1], seconds=args[2])


@contextmanager
def cd(path):
    """ A context manager that changes into given directory on __enter__,
        change back to original_file directory on exit. Exception safe.

    """
    old_dir = os.getcwd()
    os.chdir(path)

    try:
        yield
    finally:
        os.chdir(old_dir)


def submit_job(*args, **kwargs):
    session = ParallelSession(*args, **kwargs)
    session.run()


HOST_POOL = ['ecrawf6@cs-{}.cs.mcgill.ca'.format(i) for i in range(1, 32)]


class ParallelSession(object):
    """ Run a Job in parallel using gnu-parallel.

    A directory for this Job execution is created in `scratch`, and results are saved there.

    Parameters
    ----------
    name: str
        Name for the experiment.
    input_zip: str
        Path to a zip archive storing the Job.
    pattern: str
        Pattern to use to select which ops to run within the Job.
    scratch: str
        Path to location where the results of running the selected ops will be
        written. Must be writeable by the master process.
    local_scratch_prefix: str
        Path to scratch directory that is local to each remote process.
    ppn: int
        Number of processors per node.
    wall_time: str
        String specifying the maximum wall-time allotted to running the selected ops.
    cleanup_time: str
        String specifying the amount of time required to clean-up. Job execution will be
        halted when there is this much time left in the overall wall_time, at which point
        results computed so far will be collected.
    add_date: bool
        Whether to add current date/time to the name of the directory where results are stored.
    time_slack: int
        Number of extra seconds to allow per job.
    dry_run: bool
        If True, control script will be generated but not executed/submitted.
    parallel_exe: str
        Path to the `gnu-parallel` executable to use.
    host_pool: list of str
        A list of names of hosts to use to execute the job.
    max_hosts: int
        Maximum number of hosts to use.
    env_vars: dict (str -> str)
        Dictionary mapping environment variable names to values. These will be accessible
        by the submit script, and will also be sent to the worker nodes.
    redirect: bool
        If True, stderr and stdout of jobs is saved in files rather than being printed to screen.
    n_retries: int
        Number of retries per job.

    """
    def __init__(
            self, name, input_zip, pattern, scratch, local_scratch_prefix='/tmp/dps/hyper/', ppn=12,
            wall_time="1:00:00", cleanup_time="00:15:00", time_slack=0,
            add_date=True, dry_run=0, parallel_exe="$HOME/.local/bin/parallel",
            host_pool=None, min_hosts=1, max_hosts=1, env_vars=None, redirect=False, n_retries=0):
        input_zip = Path(input_zip)
        input_zip_abs = input_zip.resolve()
        input_zip_base = input_zip.name
        input_zip_stem = input_zip.stem
        archive_root = zip_root(input_zip)
        clean_pattern = pattern.replace(' ', '_')

        # Create directory to run the job from - should be on scratch.
        scratch = os.path.abspath(scratch)
        job_directory = make_directory_name(
            scratch,
            '{}_{}'.format(name, clean_pattern),
            add_date=add_date)
        os.makedirs(os.path.realpath(job_directory + "/results"))

        # storage local to each node, from the perspective of that node
        local_scratch = str(Path(local_scratch_prefix) / Path(job_directory).name)

        cleanup_time = parse_timedelta(cleanup_time)
        try:
            wall_time = parse_timedelta(wall_time)
        except:
            deadline = parse_date(wall_time)
            wall_time = deadline - datetime.datetime.now()
            if int(wall_time.total_seconds()) < 0:
                raise Exception("Deadline {} is in the past!".format(deadline))

        if cleanup_time > wall_time:
            raise Exception("Cleanup time {} is larger than wall_time {}!".format(cleanup_time, wall_time))

        wall_time_seconds = int(wall_time.total_seconds())
        cleanup_time_seconds = int(cleanup_time.total_seconds())

        redirect = "--redirect" if redirect else ""

        env = os.environ.copy()

        env_vars = env_vars or {}
        env_vars["OMP_NUM_THREADS"] = 1

        env.update({e: str(v) for e, v in env_vars.items()})
        env_vars = ' '.join('--env ' + k for k in env_vars)

        ro_job = ReadOnlyJob(input_zip)
        indices_to_run = sorted([op.idx for op in ro_job.ready_incomplete_ops(sort=False)])
        n_jobs_to_run = len(indices_to_run)
        if n_jobs_to_run == 0:
            print("All jobs are finished! Exiting.")
            return

        self.min_hosts = min_hosts
        self.max_hosts = max_hosts
        self.hosts = []
        self.host_pool = host_pool or HOST_POOL
        self.__dict__.update(locals())

        with cd(job_directory):
            # Get an estimate of the number of hosts we'll have available.
            self.recruit_hosts()

        node_file = " --sshloginfile nodefile.txt "

        n_nodes = len(self.hosts)

        if n_jobs_to_run < self.n_procs:
            n_steps = 1
            n_nodes = int(np.ceil(n_jobs_to_run / ppn))
            n_procs = n_nodes * ppn
            self.hosts = self.hosts[:n_nodes]
        else:
            n_steps = int(np.ceil(n_jobs_to_run / self.n_procs))

        execution_time = int((wall_time - cleanup_time).total_seconds())
        abs_seconds_per_step = int(np.floor(execution_time / n_steps))
        seconds_per_step = abs_seconds_per_step - time_slack

        assert execution_time > 0
        assert abs_seconds_per_step > 0
        assert seconds_per_step > 0

        staged_hosts = set()

        self.__dict__.update(locals())

        # Create convenience `latest` symlinks
        make_symlink(job_directory, os.path.join(scratch, 'latest'))

    def recruit_hosts(self, max_procs=np.inf):
        self.hosts = []
        for host in self.host_pool:
            n_hosts = len(self.hosts)
            if n_hosts >= self.max_hosts:
                break

            if n_hosts * self.ppn >= max_procs:
                break

            print("\n" + ("~" * 40))
            print("Recruiting host {}...".format(host))

            if host is not ':':
                print("Testing connection...")
                failed = self.ssh_execute("echo Connected to \$HOSTNAME", host, robust=True)
                if failed:
                    print("Could not connect.")
                    continue

            print("Preparing host...")
            try:
                if host is ':':
                    command = "stat {local_scratch}"
                    create_local_scratch = self.execute_command(command, robust=True)

                    if create_local_scratch:
                        print("Creating local scratch directory...")
                        command = "mkdir -p {local_scratch}"
                        self.execute_command(command, robust=False)

                    command = "cd {local_scratch} && stat {archive_root}"
                    missing_archive = self.execute_command(command, robust=True)

                    if missing_archive:
                        command = "cd {local_scratch} && stat {input_zip_base}"
                        missing_zip = self.execute_command(command, robust=True)

                        if missing_zip:
                            print("Copying zip to local scratch...")
                            command = "cp {input_zip_abs} {local_scratch}".format(**self.__dict__)
                            self.execute_command(command, frmt=False, robust=False)

                        print("Unzipping...")
                        command = "cd {local_scratch} && unzip -ouq {input_zip_base}"
                        self.execute_command(command, robust=False)

                else:
                    command = "stat {local_scratch}"
                    create_local_scratch = self.ssh_execute(command, host, robust=True)

                    if create_local_scratch:
                        print("Creating local scratch directory...")
                        command = "mkdir -p {local_scratch}"
                        self.ssh_execute(command, host, robust=False)

                    command = "cd {local_scratch} && stat {archive_root}"
                    missing_archive = self.ssh_execute(command, host, robust=True)

                    if missing_archive:
                        command = "cd {local_scratch} && stat {input_zip_base}"
                        missing_zip = self.ssh_execute(command, host, robust=True)

                        if missing_zip:
                            print("Copying zip to local scratch...")
                            command = (
                                "scp -q -oPasswordAuthentication=no -oStrictHostKeyChecking=no "
                                "-oConnectTimeout=5 -oServerAliveInterval=2 "
                                "{input_zip_abs} {host}:{local_scratch}".format(host=host, **self.__dict__)
                            )
                            self.execute_command(command, frmt=False, robust=False)

                        print("Unzipping...")
                        command = "cd {local_scratch} && unzip -ouq {input_zip_base}"
                        self.ssh_execute(command, host, robust=False)

                print("Host successfully prepared.")
                self.hosts.append(host)

            except subprocess.CalledProcessError:
                print("Preparation of host failed.")

        if len(self.hosts) < self.min_hosts:
            raise Exception(
                "Found only {} usable hosts, but minimum "
                "required hosts is {}.".format(len(self.hosts), self.min_hosts))

        if len(self.hosts) < self.max_hosts:
            print("{} hosts were requested, "
                  "but only {} usable hosts could be found.".format(self.max_hosts, len(self.hosts)))

        with open('nodefile.txt', 'w') as f:
            f.write('\n'.join(self.hosts))
        self.n_procs = self.ppn * len(self.hosts)

    def execute_command(
            self, command, frmt=True, shell=True, robust=False, max_seconds=None,
            progress=False, verbose=False, quiet=True):
        """ Uses `subprocess` to execute `command`. """

        p = None
        try:
            assert isinstance(command, str)
            if frmt:
                command = command.format(**self.__dict__)

            if verbose:
                print("\nExecuting command: " + (">" * 40) + "\n")
                print(command)

            if not shell:
                command = command.split()

            stdout = subprocess.DEVNULL if quiet else None
            stderr = subprocess.DEVNULL if quiet else None

            start = time.time()
            p = subprocess.Popen(command, shell=shell, universal_newlines=True, stdout=stdout, stderr=stderr)

            if progress:
                progress = progressbar.ProgressBar(
                    widgets=['[', progressbar.Timer(), '] ', '(', progressbar.ETA(), ') ', progressbar.Bar()],
                    max_value=max_seconds or progressbar.UnknownLength, redirect_stdout=True)
            else:
                progress = None

            interval_length = 1
            while True:
                try:
                    p.wait(interval_length)
                except subprocess.TimeoutExpired:
                    if progress is not None:
                        progress.update(min(int(time.time() - start), max_seconds))

                if p.returncode is not None:
                    break

            if progress is not None:
                progress.finish()

            if verbose:
                print("\nCommand took {} seconds.\n".format(time.time() - start))

            if p.returncode != 0:
                if isinstance(command, list):
                    command = ' '.join(command)

                print("The following command returned with non-zero exit code {}:\n    {}".format(p.returncode, command))

                if robust:
                    return p.returncode
                else:
                    raise subprocess.CalledProcessError(p.returncode, command)

            return p.returncode
        except BaseException as e:
            if p is not None:
                p.terminate()
                p.kill()
            if progress is not None:
                progress.finish()
            raise_with_traceback(e)

    def ssh_execute(self, command, host, **kwargs):
        return self.execute_command(
            "ssh -oPasswordAuthentication=no -oStrictHostKeyChecking=no "
            "-oConnectTimeout=5 -oServerAliveInterval=2 "
            "-T {host} \"{command}\"".format(host=host, command=command), **kwargs)

    def fetch(self):
        for i, host in enumerate(self.hosts):
            if host is ':':
                command = "cd {local_scratch} && zip -rq results {archive_root}"
                self.execute_command(command, robust=True)

                command = "cp {local_scratch}/results.zip ./results/{i}.zip".format(i=i, **self.__dict__)
                self.execute_command(command, frmt=False, robust=True)
            else:
                command = "cd {local_scratch} && zip -rq results {archive_root}"
                self.ssh_execute(command, host, robust=True)

                command = (
                    "scp -q -oPasswordAuthentication=no -oStrictHostKeyChecking=no "
                    "-oConnectTimeout=5 -oServerAliveInterval=2 "
                    "{host}:{local_scratch}/results.zip ./results/{i}.zip".format(
                        host=host, i=i, **self.__dict__)
                )
                self.execute_command(command, frmt=False, robust=True)

    def step(self, i, indices_for_step):
        print("Beginning step {} at: ".format(i) + "=" * 90)
        print(datetime.datetime.now())

        if not indices_for_step:
            print("No jobs left to run on step {}.".format(i))
            return

        indices_for_step = ' '.join(str(i) for i in indices_for_step)

        parallel_command = (
            "cd {local_scratch} && "
            "dps-hyper run {archive_root} {pattern} {{}} --max-time {seconds_per_step} "
            "--log-root {local_scratch} --log-name experiments {redirect}"
        )

        command = (
            '{parallel_exe} --timeout {abs_seconds_per_step} --no-notice -j {ppn} \\\n'
            '    --joblog {job_directory}/job_log.txt {node_file} \\\n'
            '    --env PATH --env LD_LIBRARY_PATH {env_vars} -v \\\n'
            '    "' + parallel_command + '" \\\n'
            '    ::: {indices_for_step}'
        )
        command = command.format(
            indices_for_step=indices_for_step, **self.__dict__)

        self.execute_command(
            command, frmt=False, robust=True,
            max_seconds=self.abs_seconds_per_step, progress=True,
            quiet=False, verbose=True)

    def checkpoint(self, i):
        print("Fetching results of step {} at: ".format(i))
        print(datetime.datetime.now())

        self.fetch()

        print("Unzipping results from nodes...")
        results_files = glob.glob("results/*.zip")

        for f in results_files:
            self.execute_command("unzip -nuq {} -d results".format(f), robust=True)

        for f in results_files:
            self.execute_command("rm -rf {}".format(f), robust=True)

        with cd('results'):
            self.execute_command("zip -rq ../results.zip {archive_root}", robust=True)

        self.execute_command("dps-hyper summary results.zip", robust=True, quiet=False)
        self.execute_command("dps-hyper view results.zip", robust=True, quiet=False)

    def run(self):
        if self.dry_run:
            print("Dry run, so not running.")
            return

        with cd(self.job_directory):
            print("\n" + ("=" * 80))
            print("Starting job at {}".format(datetime.datetime.now()))

            print("We have {wall_time_seconds} seconds to complete {n_jobs_to_run} "
                  "sub-jobs (grouped into {n_steps} steps) using {n_procs} processors.".format(**self.__dict__))
            print("{execution_time} seconds have been reserved for job execution, "
                  "and {cleanup_time_seconds} seconds have been reserved for cleanup.".format(**self.__dict__))
            print("Each step has been allotted {abs_seconds_per_step} seconds, "
                  "{seconds_per_step} seconds of which is pure computation time.\n".format(**self.__dict__))

            job = ReadOnlyJob(self.input_zip)
            subjobs_remaining = sorted([op.idx for op in job.ready_incomplete_ops(sort=False)])

            n_failures = defaultdict(int)
            dead_jobs = set()

            i = 0
            while subjobs_remaining:
                self.recruit_hosts(len(subjobs_remaining))

                indices_for_step = subjobs_remaining[:self.n_procs]
                self.step(i, indices_for_step)
                self.checkpoint(i)

                job = ReadOnlyJob('results.zip')

                subjobs_remaining = set([op.idx for op in job.ready_incomplete_ops(sort=False)])

                for j in indices_for_step:
                    if j in subjobs_remaining:
                        n_failures[j] += 1
                        if n_failures[j] > self.n_retries:
                            print("All {} attempts at completing job with index {} have failed, "
                                  "permanently removing it from set of eligible jobs.".format(n_failures[j], j))
                            dead_jobs.add(j)

                subjobs_remaining = [idx for idx in subjobs_remaining if idx not in dead_jobs]
                subjobs_remaining = sorted(subjobs_remaining)

                i += 1

            self.execute_command("cp -f {input_zip_abs} {input_zip_abs}.bk")
            self.execute_command("cp -f results.zip {input_zip_abs}")


def submit_job_pbs(
        input_zip, pattern, scratch, local_scratch_prefix='/tmp/dps/hyper/', n_nodes=1, ppn=12,
        wall_time="1:00:00", cleanup_time="00:15:00", time_slack=0,
        add_date=True, show_script=0, dry_run=0, parallel_exe="$HOME/.local/bin/parallel",
        queue=None, hosts=None, env_vars=None):
    """ Submit a Job to be executed in parallel.

    A directory for this Job execution is created in `scratch`, and results are saved there.

    Parameters
    ----------
    input_zip: str
        Path to a zip archive storing the Job.
    pattern: str
        Pattern to use to select which ops to run within the Job.
    scratch: str
        Path to location where the results of running the selected ops will be
        written. Must be writeable by the master process.
    local_scratch_prefix: str
        Path to scratch directory that is local to each remote process.
    ppn: int
        Number of processors per node.
    wall_time: str
        String specifying the maximum wall-time allotted to running the selected ops.
    cleanup_time: str
        String specifying the amount of time required to clean-up. Job execution will be
        halted when there is this much time left in the overall wall_time, at which point
        results computed so far will be collected.
    add_date: bool
        Whether to add current date/time to the name of the directory where results are stored.
    time_slack: int
        Number of extra seconds to allow per job.
    show_script: bool
        Whether to print to console the control script that is generated.
    dry_run: bool
        If True, control script will be generated but not executed/submitted.
    parallel_exe: str
        Path to the `gnu-parallel` executable to use.
    queue: str
        The queue to submit job to.
    env_vars: dict (str -> str)
        Dictionary mapping environment variable names to values. These will be accessible
        by the submit script, and will also be sent to the worker nodes.

    """
    input_zip = Path(input_zip)
    input_zip_abs = input_zip.resolve()
    input_zip_base = input_zip.name
    archive_root = zip_root(input_zip)
    name = Path(input_zip).stem
    queue = "#PBS -q {}".format(queue) if queue is not None else ""
    clean_pattern = pattern.replace(' ', '_')
    stderr = "| tee -a /dev/stderr"

    # Create directory to run the job from - should be on scratch.
    scratch = os.path.abspath(scratch)
    job_directory = make_directory_name(
        scratch,
        '{}_{}'.format(name, clean_pattern),
        add_date=add_date)
    os.makedirs(os.path.realpath(job_directory))

    # storage local to each node, from the perspective of that node
    local_scratch = '\\$RAMDISK'

    cleanup_time = parse_timedelta(cleanup_time)
    wall_time = parse_timedelta(wall_time)
    if cleanup_time > wall_time:
        raise Exception("Cleanup time {} is larger than wall_time {}!".format(cleanup_time, wall_time))

    wall_time_seconds = int(wall_time.total_seconds())
    cleanup_time_seconds = int(cleanup_time.total_seconds())

    node_file = " --sshloginfile $PBS_NODEFILE "

    idx_file = 'job_indices.txt'

    kwargs = locals().copy()

    env = os.environ.copy()
    if env_vars is not None:
        env.update({e: str(v) for e, v in env_vars.items()})
        kwargs['env_vars'] = ' '.join('--env ' + k for k in env_vars)
    else:
        kwargs['env_vars'] = ''

    ro_job = ReadOnlyJob(input_zip)
    completion = ro_job.completion(pattern)
    n_jobs_to_run = completion['n_ready_incomplete']
    if n_jobs_to_run == 0:
        print("All jobs are finished! Exiting.")
        return
    kwargs['n_jobs_to_run'] = n_jobs_to_run

    execution_time = int((wall_time - cleanup_time).total_seconds())
    total_compute_time = n_procs * execution_time
    abs_seconds_per_job = int(np.floor(total_compute_time / n_jobs_to_run))
    seconds_per_job = abs_seconds_per_job - time_slack

    assert execution_time > 0
    assert total_compute_time > 0
    assert abs_seconds_per_job > 0
    assert seconds_per_job > 0

    kwargs['abs_seconds_per_job'] = abs_seconds_per_job
    kwargs['seconds_per_job'] = seconds_per_job
    kwargs['execution_time'] = execution_time

    stage_data_code = '''
{parallel_exe} --no-notice {node_file} --nonall {env_vars} \\
"cp {input_zip_abs} {local_scratch}
'''

    fetch_results_code = '''
{parallel_exe} --no-notice {node_file} --nonall {env_vars} \\
"cp {local_scratch}/results.zip {job_directory}/results/\\$(hostname).zip
'''

    code = '''#!/bin/bash
# MOAB/Torque submission script for multiple, dynamically-run serial jobs
#
#PBS -V
#PBS -l nodes={n_nodes}:ppn={ppn},wall_time={wall_time}
#PBS -N {name}_{clean_pattern}
#PBS -M eric.crawford@mail.mcgill.ca
#PBS -m abe
#PBS -A jim-594-aa
#PBS -e stderr.txt
#PBS -o stdout.txt
{queue}

# Turn off implicit threading in Python
export OMP_NUM_THREADS=1

cd {job_directory}
mkdir results

echo Starting job at {stderr}
date {stderr}

echo Printing local scratch directories... {stderr}

{parallel_exe} --no-notice {node_file} --nonall {env_vars} -k \\
    "cd {local_scratch} && echo Local scratch on host \\$(hostname) is {local_scratch}, working directory is \\$(pwd)."

echo Staging input archive... {stderr}

''' + stage_data_code + '''

echo Unzipping... {stderr}

{parallel_exe} --no-notice {node_file} --nonall {env_vars} -k \\
    "cd {local_scratch} && unzip -ouq {input_zip_base} && echo \\$(hostname) && ls"

echo We have {wall_time_seconds} seconds to complete {n_jobs_to_run} sub-jobs using {n_procs} processors.
echo {execution_time} seconds have been reserved for job execution, and {cleanup_time_seconds} seconds have been reserved for cleanup.
echo Each sub-job has been allotted {abs_seconds_per_job} seconds, {seconds_per_job} seconds of which is pure computation time.

echo Launching jobs at {stderr}
date {stderr}

start=$(date +%s)

# Requires a newish version of parallel, has to accept --timeout
{parallel_exe} --timeout {abs_seconds_per_job} --no-notice -j {ppn} --retries 10 \\
    --joblog {job_directory}/job_log.txt {node_file} \\
    --env OMP_NUM_THREADS --env PATH --env LD_LIBRARY_PATH {env_vars} \\
    "cd {local_scratch} && dps-hyper run {archive_root} {pattern} {{}} --max-time {seconds_per_job}" < {idx_file}

end=$(date +%s)

runtime=$((end-start))

echo Executing jobs took $runtime seconds.

echo Fetching results at {stderr}
date {stderr}

{parallel_exe} --no-notice {node_file} --nonall {env_vars} -k \\
    "cd {local_scratch} && echo Zipping results on node \\$(hostname). && zip -rq results {archive_root} && ls"

''' + fetch_results_code + '''

cd results

echo Unzipping results from nodes... {stderr}

if test -n "$(find . -maxdepth 1 -name '*.zip' -print -quit)"; then
    echo Results files exist: {stderr}
    ls
else
    echo Did not find any results files from nodes. {stderr}
    echo Contents of results directory is: {stderr}
    ls
    exit 1
fi

for f in *zip
do
    echo Storing contents of $f {stderr}
    unzip -nuq $f
done

echo Zipping final results... {stderr}
zip -rq {name} {archive_root}
mv {name}.zip ..
cd ..

dps-hyper summary {name}.zip
dps-hyper view {name}.zip
mv {input_zip_abs} {input_zip_abs}.bk
cp -f {name}.zip {input_zip_abs}

'''
    code = code.format(**kwargs)
    if show_script:
        print("\n")
        print("-" * 20 + " BEGIN SCRIPT " + "-" * 20)
        print(code)
        print("-" * 20 + " END SCRIPT " + "-" * 20)
        print("\n")

    # Create convenience `latest` symlinks
    make_symlink(job_directory, os.path.join(scratch, 'latest'))
    make_symlink(
        job_directory,
        os.path.join(scratch, 'latest_{}_{}'.format(name, clean_pattern)))

    os.chdir(job_directory)
    with open(idx_file, 'w') as f:
        [f.write('{}\n'.format(u)) for u in range(completion['n_ops'])]

    submit_script = "submit_script.sh"
    with open(submit_script, 'w') as f:
        f.write(code)

    if dry_run:
        print("Dry run, so not submitting.")
    else:
        try:
            command = ['qsub', submit_script]
            print("Submitting.")
            output = subprocess.check_output(command, stderr=subprocess.STDOUT, env=env)
            output = output.decode()
            print(output)

            # Create a file in the directory with the job_id as its name
            job_id = output.split('.')[0]
            open(job_id, 'w').close()
            print("Job ID: {}".format(job_id))

        except subprocess.CalledProcessError as e:
            print("CalledProcessError has been raised while executing command: {}.".format(' '.join(command)))
            print("Output of process: ")
            print(e.output.decode())
            raise_with_traceback(e)


def _submit_job():
    """ Entry point for `dps-submit` command-line utility. """
    from clify import command_line
    command_line(submit_job, collect_kwargs=1, verbose=True)()
