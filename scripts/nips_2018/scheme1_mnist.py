"""
In training scheme 1, we don't anneal anything. There is an initial round of training without RL,
followed by a fragment wherein the exploration is gradually decreased.

There are 3 main hyper-parameters:

    * area_weight during stage 0
    * area_weight during remaining stages
    * nonzero_weight during remaining stages

"""
import clify
from dps.env.advanced import yolo_rl
from dps.datasets import EMNIST_ObjectDetection


def prepare_func():
    from dps import cfg
    cfg.curriculum[0]["area_weight"] = cfg.stage0_area_weight


distributions = dict(
    nonzero_weight=[10., 20., 30., 40., 50., 60.],
    area_weight=[.25, .5, 1., 2., 4., 8.],
    stage0_area_weight=[.01, .02, .04, .08, .16]
)


config = yolo_rl.good_config.copy(
    prepare_func=prepare_func,
    patience=10000,
    render_step=100000,
    lr_schedule=1e-4,
    max_overlap=40,
    hooks=[],
    n_val=16,
    eval_step=1000,
    max_steps=100000,

    dynamic_partition=True,
    fix_values=dict(),

    curriculum=[
        dict(fix_values=dict(obj=1), dynamic_partition=False, max_steps=10000),
        dict(obj_exploration=0.2,),
        dict(obj_exploration=0.1,),
        dict(obj_exploration=0.1, lr_schedule=1e-5),
        dict(obj_exploration=0.1, lr_schedule=1e-6),
    ],
)

# Create the datasets if necessary.
with config:
    train = EMNIST_ObjectDetection(n_examples=int(config.n_train), shuffle=True, example_range=(0.0, 0.9))
    val = EMNIST_ObjectDetection(n_examples=int(config.n_val), shuffle=True, example_range=(0.9, 1.))

from dps.hyper import build_and_submit
clify.wrap_function(build_and_submit)(config=config, distributions=distributions)