import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import animation, patches
from pathlib import Path

from dps import CoreNetwork
from dps.register import RegisterBank
from dps.environment import RegressionDataset, RegressionEnv
from dps.utils import default_config
from dps.production_system import ProductionSystemTrainer
from dps.mnist import char_to_idx, load_emnist, MnistPretrained, MnistConfig


def digits_to_numbers(digits, base=10, axis=-1, keepdims=False):
    """ Assumes little-endian (least-significant stored first). """
    mult = base ** np.arange(digits.shape[axis])
    shape = [1] * digits.ndim
    shape[axis] = mult.shape[axis]
    mult = mult.reshape(shape)
    return (digits * mult).sum(axis=axis, keepdims=keepdims)


def numbers_to_digits(numbers, shape, base=10):
    numbers = numbers.copy()
    digits = []
    for i in range(shape):
        digits.append(numbers % base)
        numbers //= base
    return np.stack(digits, -1)


class Container(object):
    def __init__(self, X, Y):
        assert len(X) == len(Y)
        self.X, self.Y = X, Y

    def get_random(self):
        idx = np.random.randint(len(self.X))
        return self.X[idx], self.Y[idx]


class SimpleArithmeticDataset(RegressionDataset):
    def __init__(
            self, mnist, symbols, shape, n_digits, upper_bound, base, blank_char,
            n_examples, op_loc, for_eval=False, shuffle=True):

        assert 1 <= base <= 10

        # symbols is a list of pairs of the form (letter, reduction function)
        self.mnist = mnist
        self.symbols = symbols
        self.shape = shape
        self.n_digits = n_digits
        self.upper_bound = upper_bound
        self.base = base
        self.blank_char = blank_char
        self.op_loc = op_loc

        assert np.product(shape) >= n_digits + 1

        if self.mnist:
            functions = {char_to_idx(s): f for s, f in symbols}

            emnist_x, emnist_y, symbol_map = load_emnist(list(functions.keys()))
            emnist_x = emnist_x.reshape(-1, 28, 28)
            emnist_y = np.squeeze(emnist_y, 1)

            functions = {symbol_map[k]: v for k, v in functions.items()}

            symbol_reps = Container(emnist_x, emnist_y)

            mnist_x, mnist_y, symbol_map = load_emnist(list(range(base)))
            mnist_x = mnist_x.reshape(-1, 28, 28)
            mnist_y = np.squeeze(mnist_y, 1)

            digit_reps = Container(mnist_x, mnist_y)
            blank_element = np.zeros((28, 28))
        else:
            sorted_symbols = sorted(symbols, key=lambda x: x[0])
            functions = {-(i+1): f for i, (_, f) in enumerate(sorted_symbols)}
            symbol_values = sorted(functions.keys())

            symbol_reps = Container(symbol_values, symbol_values)
            digit_reps = Container(np.arange(base), np.arange(base))

            blank_element = np.array([0])

        x, y = self.make_dataset(
            self.shape, self.n_digits, self.upper_bound, self.base,
            blank_element, symbol_reps, digit_reps,
            functions, n_examples, op_loc)

        super(SimpleArithmeticDataset, self).__init__(x, y, for_eval, shuffle)

    @staticmethod
    def make_dataset(
            shape, n_digits, upper_bound, base, blank_element,
            symbol_reps, digit_reps, functions, n_examples, op_loc):

        if n_examples == 0:
            return np.zeros((0,) + shape + blank_element.shape).astype('f'), np.zeros((0, 1)).astype('i')

        new_X, new_Y = [], []

        # Include a blank character, so it has to learn to skip over them.
        size = np.product(shape)

        element_shape = blank_element.shape
        _blank_element = blank_element.reshape((1,)*len(shape) + blank_element.shape)

        for j in range(n_examples):
            if upper_bound:
                n = np.random.randint(1, n_digits+1)
            else:
                n = n_digits

            if op_loc is None:
                indices = np.random.choice(size, n+1, replace=False)
            else:
                _op_loc = np.ravel_multi_index(op_loc, shape)
                indices = np.random.choice(size-1, n, replace=False)
                indices[indices == _op_loc] = size-1
                indices = np.append(_op_loc, indices)

            locs = list(zip(*np.unravel_index(indices, shape)))

            env = np.tile(_blank_element, shape+(1,)*len(element_shape))

            x, y = symbol_reps.get_random()
            func = functions[int(y)]
            env[locs[0]] = x

            ys = []

            for loc in locs[1:]:
                x, y = digit_reps.get_random()
                ys.append(y)
                env[loc] = x

            # new_X.append(np.uint8(255*np.minimum(o, 1)))
            new_X.append(env)
            new_Y.append(func(ys))

        new_X = np.array(new_X).astype('f')
        new_Y = np.array(new_Y).astype('i').reshape(-1, 1)
        return new_X, new_Y


class SimpleArithmeticEnv(RegressionEnv):
    def __init__(self, mnist, shape, n_digits, upper_bound, base,
                 n_train, n_val, n_test, op_loc=None, start_loc=None):
        self.mnist = mnist
        self.shape = shape
        self.n_digits = n_digits
        self.upper_bound = upper_bound
        self.base = base
        self.blank_char = blank_char = 'b'
        self.symbols = symbols = [
            ('A', lambda x: sum(x)),
            ('M', lambda x: np.product(x)),
            ('C', lambda x: len(x))]

        self.op_loc = op_loc
        self.start_loc = op_loc

        super(SimpleArithmeticEnv, self).__init__(
            train=SimpleArithmeticDataset(
                mnist, symbols, shape, n_digits, upper_bound, base, blank_char, n_train, op_loc, for_eval=False),
            val=SimpleArithmeticDataset(
                mnist, symbols, shape, n_digits, upper_bound, base, blank_char, n_val, op_loc, for_eval=True),
            test=SimpleArithmeticDataset(
                mnist, symbols, shape, n_digits, upper_bound, base, blank_char, n_test, op_loc, for_eval=True))

    def __str__(self):
        return "<SimpleArithmeticEnv shape={} base={}>".format(self.height, self.shape, self.base)


if __name__ == "__main__":

    symbols = [
        ('A', lambda x: sum(x)),
        ('M', lambda x: np.product(x)),
        ('C', lambda x: len(x))]
    env = SimpleArithmeticEnv(True, symbols, (2, 2), 1, False, 2, 10, 1, 1)


class SimpleArithmetic(CoreNetwork):
    """ Top left is (x=0, y=0). Sign is in the bottom left of the input grid.

    For now, the location of the write head is the same as the x location of the read head.

    """
    action_names = [
        'fovea_x += ', 'fovea_x -= ',
        'fovea_y += ', 'fovea_y -= ',
        'store_op', 'add', 'inc', 'multiply', 'store', 'no-op/stop']

    @property
    def input_shape(self):
        return self.shape + self.element_shape

    @property
    def element_shape(self):
        return (28, 28) if self.mnist else (1,)

    @property
    def make_input_available(self):
        return True

    def __init__(self, env):
        self.mnist = env.mnist
        self.symbols = env.symbols
        self.shape = env.shape

        if not len(self.shape) == 2:
            raise NotImplementedError("Shape must have length 2.")

        self.n_digits = env.n_digits
        self.upper_bound = env.upper_bound
        self.base = env.base
        self.blank_char = env.blank_char

        self.start_loc = env.start_loc

        build_classifier = default_config().build_classifier
        classifier_str = default_config().classifier_str

        if self.mnist:
            digit_config = MnistConfig(symbols=range(self.base))
            name = '{}_symbols={}.chk'.format(
                classifier_str, '_'.join(str(s) for s in range(self.base)))

            self.build_digit_classifier = MnistPretrained(
                None, build_classifier, name=name,
                var_scope_name='digit_classifier', config=digit_config)

            op_symbols = [10, 12, 22]
            op_config = MnistConfig(symbols=op_symbols)
            name = '{}_symbols={}.chk'.format(
                classifier_str, '_'.join(str(s) for s in op_symbols))

            self.build_op_classifier = MnistPretrained(
                None, build_classifier, name=name,
                var_scope_name='op_classifier', config=op_config)

        else:
            self.build_digit_classifier = lambda x: tf.identity(x)
            self.build_op_classifier = lambda x: tf.identity(x)

        values = (
            [0., 0., 0., 0., 0., 0., 0.] +
            [np.zeros(np.product(self.element_shape), dtype='f')])

        self.register_bank = RegisterBank(
            'SimpleArithmeticRB',
            'op acc fovea_x fovea_y vision op_vision t glimpse', None, values=values,
            output_names='acc', no_display='glimpse')

        super(SimpleArithmetic, self).__init__()

    def init(self, r, inp):
        op, acc, fovea_x, fovea_y, vision, op_vision, t, glimpse = self.register_bank.as_tuple(r)

        _fovea_x = tf.cast(fovea_x, tf.int32)
        _fovea_y = tf.cast(fovea_y, tf.int32)

        batch_size = tf.shape(inp)[0]
        indices = tf.concat([
            tf.reshape(tf.range(batch_size), (-1, 1)),
            _fovea_y,
            _fovea_x], axis=1)
        glimpse = tf.gather_nd(inp, indices)
        glimpse = tf.reshape(glimpse, (-1, np.product(self.element_shape)), name="glimpse")

        digit_classification = tf.stop_gradient(self.build_digit_classifier(glimpse))
        vision = tf.cast(tf.expand_dims(tf.argmax(digit_classification, 1), 1), tf.float32)

        op_classification = tf.stop_gradient(self.build_op_classifier(glimpse))
        op_vision = tf.cast(tf.expand_dims(tf.argmax(op_classification, 1), 1), tf.float32)

        if self.start_loc is not None:
            fovea_y = tf.fill((batch_size, 1), self.start_loc[0])
            fovea_x = tf.fill((batch_size, 1), self.start_loc[1])
        else:
            fovea_y = tf.random_uniform(tf.shape(fovea_y), 0, self.shape[0], dtype=tf.int32)
            fovea_x = tf.random_uniform(tf.shape(fovea_x), 0, self.shape[1], dtype=tf.int32)

        fovea_x = tf.cast(fovea_x, tf.float32)
        fovea_y = tf.cast(fovea_y, tf.float32)

        with tf.name_scope("SimpleArithmetic"):
            new_registers = self.register_bank.wrap(
                glimpse=glimpse,
                acc=tf.identity(acc, "acc"),
                op=tf.identity(op, "op"),
                fovea_x=tf.identity(fovea_x, "fovea_x"),
                fovea_y=tf.identity(fovea_y, "fovea_y"),
                vision=tf.identity(vision, "vision"),
                op_vision=tf.identity(op_vision, "op_vision"),
                t=tf.identity(t, "t"))

        return new_registers

    def __call__(self, action_activations, r, inp):
        _op, _acc, _fovea_x, _fovea_y, _vision, _op_vision, _t, _glimpse = self.register_bank.as_tuple(r)

        (inc_fovea_x, dec_fovea_x,
         inc_fovea_y, dec_fovea_y,
         store_op, add, inc, multiply, store, no_op) = (
            tf.split(action_activations, self.n_actions, axis=1))

        acc = (1 - add - inc - multiply - store) * _acc + \
            add * (_vision + _acc) + \
            multiply * (_vision * _acc) + \
            inc * (_acc + 1) + \
            store * _vision
        op = (1 - store_op) * _op + store_op * _op_vision

        fovea_x = (1 - inc_fovea_x - dec_fovea_x) * _fovea_x + \
            inc_fovea_x * (_fovea_x + 1) + \
            dec_fovea_x * (_fovea_x - 1)

        fovea_y = (1 - inc_fovea_y - dec_fovea_y) * _fovea_y + \
            inc_fovea_y * (_fovea_y + 1) + \
            dec_fovea_y * (_fovea_y - 1)

        _fovea_x = tf.cast(fovea_x, tf.int32)
        _fovea_y = tf.cast(fovea_y, tf.int32)

        batch_size = tf.shape(inp)[0]
        indices = tf.concat([
            tf.reshape(tf.range(batch_size), (-1, 1)),
            _fovea_y,
            _fovea_x], axis=1)
        glimpse = tf.gather_nd(inp, indices)
        glimpse = tf.reshape(glimpse, (-1, np.product(self.element_shape)), name="glimpse")

        digit_classification = tf.stop_gradient(self.build_digit_classifier(glimpse))
        vision = tf.cast(tf.expand_dims(tf.argmax(digit_classification, 1), 1), tf.float32)

        op_classification = tf.stop_gradient(self.build_op_classifier(glimpse))
        op_vision = tf.cast(tf.expand_dims(tf.argmax(op_classification, 1), 1), tf.float32)

        t = _t + 1

        with tf.name_scope("MnistArithmetic"):
            new_registers = self.register_bank.wrap(
                glimpse=glimpse,
                acc=tf.identity(acc, "acc"),
                op=tf.identity(op, "op"),
                fovea_x=tf.identity(fovea_x, "fovea_x"),
                fovea_y=tf.identity(fovea_y, "fovea_y"),
                vision=tf.identity(vision, "vision"),
                op_vision=tf.identity(op_vision, "op_vision"),
                t=tf.identity(t, "t"))

        return new_registers


def render_rollouts(psystem, actions, registers, reward, external_obs, external_step_lengths):
    """ Render rollouts from TranslatedMnist task. """
    config = default_config()
    if not config.save_display and not config.display:
        print("Skipping rendering.")
        return

    n_timesteps, batch_size, n_actions = actions.shape
    s = int(np.ceil(np.sqrt(batch_size)))

    fig, subplots = plt.subplots(2*s, s)

    env_subplots = subplots[::2, :].flatten()
    glimpse_subplots = subplots[1::2, :].flatten()

    if psystem.env.mnist:
        images = []
        for ri in external_obs[0]:
            ri = [np.concatenate(r, axis=-1) for r in ri]
            ri = np.concatenate(ri, axis=0)
            images.append(ri)
    else:
        images = np.squeeze(external_obs[0], axis=-1)

    shape = psystem.core_network.shape

    [ax.imshow(im, cmap='gray', origin='upper', extent=(0, shape[1], shape[0], 0)) for im, ax in zip(images, env_subplots)]

    rectangles = [
        ax.add_patch(patches.Rectangle(
            (1.0, 0.0), 1.0, 1.0, alpha=0.6, transform=ax.transAxes))
        for ax in env_subplots]

    glimpse_shape = psystem.core_network.element_shape
    if len(glimpse_shape) == 1:
        glimpse_shape = glimpse_shape + (1,)
    glimpses = [ax.imshow(np.random.randint(256, size=glimpse_shape), cmap='gray', origin='upper') for ax in glimpse_subplots]

    fovea_x = psystem.rb.get('fovea_x', registers)
    fovea_y = psystem.rb.get('fovea_y', registers)
    glimpse = psystem.rb.get('glimpse', registers)

    def animate(i):
        # Find locations of bottom-left in fovea co-ordinate system, then transform to axis co-ordinate system.
        fx = fovea_x[i, :, :]
        fy = fovea_y[i, :, :] + 1.0

        # use fovea to modify the rectangles
        for x, y, rect in zip(fx, fy, rectangles):
            rect.set_x(x)
            rect.set_y(y)

        for g, gimg in zip(glimpse[i, :, :], glimpses):
            gimg.set_data(g.reshape(*glimpse_shape))

        return rectangles + glimpses

    _animation = animation.FuncAnimation(fig, animate, n_timesteps, blit=True, interval=1000, repeat=True)

    if default_config().save_display:
        Writer = animation.writers['ffmpeg']
        writer = Writer(fps=15, metadata=dict(artist='Me'), bitrate=1800)
        _animation.save(str(Path(default_config().path) / 'animation.mp4'), writer=writer)

    if default_config().display:
        plt.show()


class SimpleArithmeticTrainer(ProductionSystemTrainer):
    def build_env(self):
        config = default_config()
        return SimpleArithmeticEnv(
            config.mnist, config.shape, config.n_digits,
            config.upper_bound, config.base, config.n_train, config.n_val, config.n_test,
            config.op_loc, config.start_loc)

    def build_core_network(self, env):
        return SimpleArithmetic(env)
