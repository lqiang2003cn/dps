from dps.utils import DpsConfig


distributions = dict(
    n_controller_units=[32, 64, 128, 256],
    exploration_schedule=[
        'constant 1.0',
        'constant 0.1',

        'exp 1.0 100000 0.01',
        'exp 1.0 100000 0.1',
    ],
    lr_schedule=[
        'constant 0.00025',
        'constant 0.001',
        'constant 0.01',

        'exp 0.01 100000 0.00025',
        'exp 0.1 100000 0.00025',
        'exp 1 100000 0.00025',
    ],
    replay_max_size=[512, 5000, 50000],
    replay_proportion=[0.0, 0.25, 0.5],
    steps_per_target_update=[1000, 5000, 10000],
    samples_per_update=[4, 8, 16, 32, 64],
    update_batch_size=[32, 64, 128, 256],
    double=[True, False]
)


class Config(DpsConfig):
    curriculum = [
        dict(T=10, shape=(2, 2), n_digits=2, upper_bound=True),
        dict(T=15, shape=(3, 3), n_digits=2, upper_bound=True),
        dict(T=25, shape=(4, 4), n_digits=2, upper_bound=True),
        dict(T=30, shape=(5, 5), n_digits=2, upper_bound=True),
        # dict(T=2),
        # dict(T=3),
        # dict(T=4),
        # dict(T=5),
        # dict(T=10),
        # dict(T=10, shape=(2, 2)),
        # dict(T=10, n_digits=2, shape=(2, 2)),
        # dict(T=15, n_digits=2, shape=(2, 2)),
        # dict(T=15, n_digits=2, shape=(3, 2)),
        # dict(T=15, n_digits=2, shape=(3, 3)),
        # dict(T=20, n_digits=2, shape=(3, 3)),
        # dict(T=20, n_digits=2, shape=(4, 4)),
        # dict(T=20, n_digits=2, shape=(4, 4)),
    ]
    base = 10
    gamma = 0.99
    upper_bound = True
    mnist = 0
    op_loc = (0, 0)
    start_loc = (0, 0)

    batch_size = 256
    power_through = False
    optimizer_spec = 'rmsprop'
    max_steps = 100000
    preserve_policy = True
    start_tensorboard = False
    verbose = 0
    visualize = False

    reward_window = 0.5
    test_time_explore = 0.1
    threshold = 0.05
    patience = 5000

    noise_schedule = None

    display_step = 1000
    eval_step = 100
    checkpoint_step = 0
    use_gpu = 0
    slim = True
    n_val = 5000


if __name__ == "__main__":
    from dps.parallel.hyper import build_search

    config = Config()

    path = '/tmp/dps/jobs'
    name = 'simple_arithmetic_monday_after_guys_weekend'
    n = 300
    repeats = 10
    alg = 'qlearning'
    task = 'simple_arithmetic'
    job = build_search(path, name, n, repeats, alg, task, False, distributions, config)
    job.run('map', None, False, False)
    job.run('reduce', None, False, False)
