import clify
import argparse

from dps.config import DEFAULT_CONFIG

from dps.projects.nips_2018 import envs
from dps.env.advanced.yolo_math import convolutional_config

parser = argparse.ArgumentParser()
parser.add_argument("duration", choices="long med".split())
parser.add_argument("size", choices="14 21 28".split())
parser.add_argument("--c", action="store_true")
args, _ = parser.parse_known_args()
duration = args.duration


distributions = dict(
    n_train=[1000, 2000, 4000, 8000, 16000, 32000],
)

env_config = envs.get_mnist_config(size=args.size, colour=args.c, task=args.task)


config = DEFAULT_CONFIG.copy()

config.update(convolutional_config)
config.update(env_config)

# Feedforward network needs a different environment
config.update(build_env=convolutional_config.build_env)

config.update(
    render_step=5000,
    eval_step=1000,
    per_process_gpu_memory_fraction=0.3,

    patience=5000,
    max_experiences=100000000,
    max_steps=110000,

    curriculum=[
        dict()
    ]
)

config.log_name = "sample_complexity_experiment_{}_alg={}".format(env_config.log_name, yolo_math_config.log_name)

run_kwargs = dict(
    n_repeats=6,
    kind="slurm",
    pmem=5000,
    ignore_gpu=False,
)

if duration == "long":
    duration_args = dict(
        max_hosts=1, ppn=6, cpp=2, gpu_set="0,1", wall_time="6hours", project="rpp-bengioy",
        step_time_limit="2hours", cleanup_time="10mins", slack_time="5mins")

elif duration == "med":
    config.max_steps=1000
    duration_args = dict(
        max_hosts=1, ppn=3, cpp=2, gpu_set="0", wall_time="1hour", project="rpp-bengioy",
        cleanup_time="10mins", slack_time="3mins", n_param_settings=6, n_repeats=1, step_time_limit="25mins")

else:
    raise Exception("Unknown duration: {}".format(duration))

run_kwargs.update(duration_args)

readme = "Running sample complexity experiment on {} task with convolutional network.".format(args.task)

from dps.hyper import build_and_submit
clify.wrap_function(build_and_submit)(
    name=config.log_name + "_duration={}".format(duration),
    config=config, readme=readme, distributions=distributions, **run_kwargs)