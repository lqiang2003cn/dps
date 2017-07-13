import tensorflow as tf
import numpy as np

from dps import cfg
from dps.register import RegisterBank
from dps.environment import (
    RegressionDataset, RegressionEnv, CompositeEnv, TensorFlowEnv)
from dps.vision.attention import apply_gaussian_filter


class SimpleAdditionDataset(RegressionDataset):
    def __init__(self, width, n_digits, n_examples):
        self.width = width
        self.n_digits = n_digits

        x = np.random.randint(0, n_digits, size=(n_examples, 2*width+1))
        y = x[:, :1] + x[:, -1:]
        super(SimpleAdditionDataset, self).__init__(x, y)


class SimpleAdditionEnv(RegressionEnv):
    def __init__(self, width, n_digits, n_train, n_val):
        self.width = width
        self.n_digits = n_digits
        super(SimpleAdditionEnv, self).__init__(
            train=SimpleAdditionDataset(width, n_digits, n_train),
            val=SimpleAdditionDataset(width, n_digits, n_val))

    def __str__(self):
        return "<SimpleAdditionEnv - width={}, n_digits={}>".format(self.width, self.n_digits)


class SimpleAddition(TensorFlowEnv):
    action_names = ['fovea += 1', 'fovea -= 1', 'wm1 = vision', 'wm2 = vision',
                    'output = vision', 'output = wm1 + wm2', 'no-op/stop']

    @property
    def input_shape(self):
        return (2*self.width+1,)

    make_input_available = True

    def __init__(self, env):
        self.width = env.width
        self.rb = RegisterBank(
            'SimpleAdditionRB',
            'fovea vision wm1 wm2 output', None,
            values=([0.] * 5),
            output_names='output')
        super(SimpleAddition, self).__init__()

    def static_inp_type_and_shape(self):
        return (tf.float32, (2*self.width+1,))

    def build_init(self, r, inp):
        fovea, vision, wm1, wm2, output = self.rb.as_tuple(r)
        std = tf.fill(tf.shape(fovea), 0.01)
        locations = tf.constant(np.linspace(-self.width, self.width, 2*self.width+1, dtype='f'), dtype=tf.float32)
        vision = apply_gaussian_filter(fovea, std, locations, inp)

        new_registers = self.rb.wrap(
            fovea=fovea, vision=vision, wm1=wm1, wm2=wm2, output=output)

        return new_registers

    def build_step(self, t, r, a, static_inp):
        _fovea, _vision, _wm1, _wm2, _output = self.rb.as_tuple(r)

        inc_fovea, dec_fovea, vision_to_wm1, vision_to_wm2, vision_to_output, add, no_op = (
            tf.split(a, self.n_actions, axis=1))

        fovea = (1 - inc_fovea - dec_fovea) * _fovea + inc_fovea * (_fovea + 1) + dec_fovea * (_fovea - 1)
        wm1 = (1 - vision_to_wm1) * _wm1 + vision_to_wm1 * _vision
        wm2 = (1 - vision_to_wm2) * _wm2 + vision_to_wm2 * _vision
        output = (1 - vision_to_output - add) * _output + vision_to_output * _vision + add * (_wm1 + _wm2)

        std = tf.fill(tf.shape(fovea), 0.01)
        locations = tf.constant(np.linspace(-self.width, self.width, 2*self.width+1, dtype='f'), dtype=tf.float32)
        vision = apply_gaussian_filter(fovea, std, locations, static_inp)

        new_registers = self.rb.wrap(
            fovea=fovea, vision=vision, wm1=wm1, wm2=wm2, output=output)

        return tf.fill((tf.shape(r)[0], 1), 0.0), new_registers


def build_env():
        external = SimpleAdditionEnv(cfg.width, cfg.n_digits, cfg.n_train, cfg.n_val)
        internal = SimpleAddition(external)
        return CompositeEnv(external, internal)
