import clify
import argparse
import numpy as np

from dps.config import DEFAULT_CONFIG

from dps.projects.nips_2018 import envs
from dps.projects.nips_2018.algs import yolo_air_config as alg_config


parser = argparse.ArgumentParser()
parser.add_argument("kind", choices="long_cedar long_graham med short".split())
args, _ = parser.parse_known_args()
kind = args.kind


distributions = dict(
    kernel_size=[1, 2, 3],
    final_count_prior_log_odds=[0.1, 0.05, 0.025, 0.0125],
    hw_prior_std=[0.0625, 0.25, 1.0, 2.0, 4.0],
)


config = DEFAULT_CONFIG.copy()
config.update(alg_config)
config.update(envs.scatter_colour_14x14_config)
config.update(
    render_step=1000,
    eval_step=1000,
    per_process_gpu_memory_fraction=0.23,

    patience=1000000,
    max_experiences=100000000,
    max_steps=100000000,
)

config.log_name = "{}_VS_{}".format(alg_config.log_name, envs.scatter_colour_14x14_config.log_name)
run_kwargs = dict(
    n_repeats=1,
    kind="slurm",
    pmem=5000,
    ignore_gpu=False,
)

if kind == "long_cedar":
    kind_args = dict(
        max_hosts=2, ppn=12, cpp=2, gpu_set="0,1,2,3", wall_time="11hours", project="rpp-bengioy",
        cleanup_time="30mins", slack_time="30mins", n_param_settings=24)

elif kind == "long_graham":
    kind_args = dict(
        max_hosts=3, ppn=8, cpp=2, gpu_set="0,1", wall_time="12hours", project="def-jpineau",
        cleanup_time="30mins", slack_time="30mins", n_param_settings=24)

elif kind == "med":
    kind_args = dict(
        max_hosts=1, ppn=8, cpp=2, gpu_set="0", wall_time="1hour", project="rpp-bengioy",
        cleanup_time="10mins", slack_time="10mins", n_param_settings=8)

elif kind == "short":
    kind_args = dict(
        max_hosts=1, ppn=4, cpp=1, gpu_set="0", wall_time="20mins", project="rpp-bengioy",
        cleanup_time="5mins", slack_time="5mins", n_param_settings=4)

else:
    raise Exception("Unknown kind: {}".format(kind))

run_kwargs.update(kind_args)

readme = "First time testing yolo_air on scattered task."

from dps.hyper import build_and_submit
clify.wrap_function(build_and_submit)(
    name="yolo_air_v_scatter_kind={}".format(kind), config=config, readme=readme,
    distributions=distributions, **run_kwargs)
