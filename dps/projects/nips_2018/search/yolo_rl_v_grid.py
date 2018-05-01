import clify
import argparse
import numpy as np

from dps import cfg
from dps.config import DEFAULT_CONFIG

from dps.projects.nips_2018 import envs
from dps.projects.nips_2018.algs import yolo_rl_config as alg_config
from dps.train import PolynomialScheduleHook


parser = argparse.ArgumentParser()
parser.add_argument("kind", choices="long_cedar long_graham short_graham short_cedar".split())
parser.add_argument("env", choices="white_14x14 colour_14x14 white_28x28 colour_28x28".split())
args, _ = parser.parse_known_args()
kind = args.kind


fragment = [
    dict(obj_exploration=0.2, preserve_env=False),
    dict(obj_exploration=0.1,),
    dict(obj_exploration=0.05,),
    dict(do_train=False, n_train=16, min_chars=1, postprocessing="", preserve_env=False),
]


distributions = dict(
    area_weight=list(np.linspace(0.1, 1.0, 24))
)

config = DEFAULT_CONFIG.copy()

config.update(alg_config)

env_config = getattr(envs, "scatter_{}_config".format(args.env))
config.update(env_config)

config.update(
    render_step=100000,
    eval_step=1000,
    per_process_gpu_memory_fraction=0.3,

    max_experiences=100000000,
    patience=2500,
    max_steps=100000000,

    area_weight=None,
    nonzero_weight=None,

    curriculum=[
        dict(do_train=False),
    ],

    hooks=[
        PolynomialScheduleHook(
            attr_name="nonzero_weight",
            query_name="best_COST_reconstruction",
            base_configs=fragment, tolerance=10,
            initial_value=50,
            scale=10, power=1.0)
    ]
)

config.log_name = "{}_VS_{}_env={}".format(alg_config.log_name, env_config.log_name, args.env)

print("Forcing creation of first dataset.")
with config.copy():
    cfg.build_env()

print("Forcing creation of second dataset.")
with config.copy(fragment[-1]):
    cfg.build_env()

run_kwargs = dict(
    n_repeats=1,
    kind="slurm",
    pmem=5000,
    ignore_gpu=False,
)

if kind == "long_cedar":
    kind_args = dict(
        max_hosts=2, ppn=12, cpp=2, gpu_set="0,1,2,3", wall_time="24hours", project="rrg-dprecup",
        cleanup_time="30mins", slack_time="30mins")

elif kind == "long_graham":
    kind_args = dict(
        max_hosts=2, ppn=8, cpp=1, gpu_set="0,1", wall_time="6hours", project="def-jpineau",
        cleanup_time="30mins", slack_time="30mins", n_param_settings=16)

elif kind == "short_cedar":
    kind_args = dict(
        max_hosts=1, ppn=3, cpp=1, gpu_set="0", wall_time="20mins", project="rrg-dprecup",
        cleanup_time="2mins", slack_time="2mins", n_param_settings=3)

elif kind == "short_graham":
    kind_args = dict(
        max_hosts=1, ppn=4, cpp=1, gpu_set="0", wall_time="20mins", project="def-jpineau",
        cleanup_time="2mins", slack_time="2mins", n_param_settings=4)

else:
    raise Exception("Unknown kind: {}".format(kind))

run_kwargs.update(kind_args)

from dps.hyper import build_and_submit
clify.wrap_function(build_and_submit)(
    name="{}_param_search_{}".format(args.env, kind), config=config,
    distributions=distributions, **run_kwargs)
