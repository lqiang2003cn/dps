import numpy as np

import clify

from dps.utils import Config
from dps.envs import grid_arithmetic
from dps.rl.algorithms import a2c
from dps.rl.policy import BuildEpsilonSoftmaxPolicy, BuildLstmController
from dps.rl import rl_render_hook
from dps.config import DEFAULT_CONFIG


config = DEFAULT_CONFIG.copy(
    name="GridArithmeticExperiments",

    n_train=10000,
    n_val=100,
    max_steps=1000000,
    display_step=100,
    eval_step=100,
    patience=np.inf,
    power_through=True,
    preserve_policy=True,

    slim=False,
    save_summaries=True,
    start_tensorboard=True,
    verbose=False,
    show_plots=True,
    save_plots=True,

    threshold=0.01,
    render_hook=rl_render_hook,
    memory_limit_mb=12*1024,
)


alg_config = Config(
    get_updater=a2c.A2C,
    build_policy=BuildEpsilonSoftmaxPolicy(),
    build_controller=BuildLstmController(),
    optimizer_spec="adam",

    policy_weight=1.0,
    value_reg_weight=0.0,
    value_weight=1.0,
    entropy_weight=0.01,

    lr_schedule=1e-4,
    n_controller_units=128,
    batch_size=16,
    gamma=0.98,
    opt_steps_per_update=1,
    epsilon=0.2,
    split=False,

    exploration_schedule="Poly(1.0, 0.1, 8192)",
    actor_exploration_schedule=None,
    val_exploration_schedule="0.0",

    q_lmbda=1.0,
    v_lmbda=1.0,
    policy_importance_c=0,
    q_importance_c=None,
    v_importance_c=None,
    max_grad_norm=None,

    updates_per_sample=1,
)

env_config = grid_arithmetic.config.copy(
    reductions="sum",
    arithmetic_actions='+,*,max,min,+1',
    ablation='easy',
    render_rollouts=None,

    T=30,
    min_digits=2,
    max_digits=3,
    op_loc=(0, 0),
    start_loc=(0, 0),
    base=10,
    threshold=0.01,

    salience_shape=(2, 2),
    salience_action=True,
    visible_glimpse=False,
    initial_salience=False,
    salience_input_width=3*14,
    salience_output_width=14,
    downsample_factor=2,

    final_reward=True,
)

config.update(alg_config)
config.update(env_config)


A_load_paths = [
    "/home/e2crawfo/checkpoints/latest_checkpoints/sum_checkpoints/networks/0/best_of_stage_0",
    "/home/e2crawfo/checkpoints/latest_checkpoints/sum_checkpoints/networks/1/best_of_stage_0",
    "/home/e2crawfo/checkpoints/latest_checkpoints/sum_checkpoints/networks/2/best_of_stage_0",
    "/home/e2crawfo/checkpoints/latest_checkpoints/sum_checkpoints/networks/3/best_of_stage_0",
    "/home/e2crawfo/checkpoints/latest_checkpoints/sum_checkpoints/networks/4/best_of_stage_0",
]


B_load_paths = [
    "/home/e2crawfo/checkpoints/checkpoints_3x3/0/best_of_stage_0",
    "/home/e2crawfo/checkpoints/checkpoints_3x3/1/best_of_stage_0",
    "/home/e2crawfo/checkpoints/checkpoints_3x3/2/best_of_stage_0",
    "/home/e2crawfo/checkpoints/checkpoints_3x3/3/best_of_stage_0",
]


config.update(
    use_gpu=True,
    gpu_allow_growth=True,
    per_process_gpu_memory_fraction=0.22,
    load_path="",

    # curriculum=[dict(shape=(3, 3), load_path=A_load_paths)],  # A -> 0
    # curriculum=[dict(shape=(3, 3))],  # C
    curriculum=[dict(shape=(3, 3), min_digits=4, max_digits=4, load_path=B_load_paths)],  # B -> A
    # curriculum=[dict(shape=(3, 3), min_digits=4, max_digits=4)],  # F
)

# grid = dict(n_train=[2**16])
grid = dict(n_train=2**np.arange(6, 18, 2))


from dps.parallel.hyper import build_and_submit
host_pool = ['ecrawf6@cs-{}.cs.mcgill.ca'.format(i) for i in range(1, 33)]
clify.wrap_function(build_and_submit)(
    config=config, distributions=grid, n_param_settings=None, host_pool=host_pool)
