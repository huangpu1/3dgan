# global
import tensorflow as tf
import numpy as np
import time
from sys import stdout
# local
from util import print_progress, fold, average_gradients, init_optimizer
from models.ops import dense, conv2d, deconv2d, lrelu, flatten, montage_summary, input_slice
from models.model import Model




class VAE(Model):
    """Variational Autoencoder with multi-GPU support."""
    
    def __init__(self, x, args):
        
        with tf.device('/cpu:0'):
            opt = init_optimizer(args)
            tower_grads = []

            with tf.variable_scope('model') as scope:
                for gpu_id in range(args.n_gpus):
                    with tf.device(self.variables_on_cpu(gpu_id)):
                        with tf.name_scope('tower_{}'.format(gpu_id)) as scope:
                            # get slice for this GPU
                            x_slice = input_slice(x, args.batch_size, gpu_id)
                            # model
                            encoder, z, z_mean, z_stddev, decoder_real, decoder_fake = self.build_model(x_slice, args, gpu_id)
                            # loss
                            g_loss, l_loss, d_loss = self.summarize_collection('losses', scope)
                            # reuse variables in next tower
                            tf.get_variable_scope().reuse_variables()
                            # add summaries for examples and samples
                            montage_summary(x_slice, args, 'inputs')
                            montage_summary(decoder_real, args, 'real')
                            montage_summary(decoder_fake, args, 'fake')
                            # compute and collect gradients for generator and discriminator
                            with tf.variable_scope('compute_gradients'):
                                tower_grads.append(opt.compute_gradients(d_loss))

            # back on the CPU
            with tf.variable_scope('training'):
                avg_grads, apply_grads, self.train_op = self.average_and_apply_grads(tower_grads, opt)
                self.summarize_grads(avg_grads)
            # combine summaries
            self.summary_op = tf.summary.merge(tf.get_collection(tf.GraphKeys.SUMMARIES, scope))
                                                   

    def build_model(self, x, args, gpu_id):
        # encoder
        with tf.variable_scope('encoder'):
            encoder = self.build_encoder(x, reuse=(gpu_id>0))
        # latent
        with tf.variable_scope('latent'):
            samples, z, z_mean, z_stddev = self.build_latent(encoder, args.batch_size, args.latent_size, reuse=(gpu_id>0))
        # decoder
        with tf.variable_scope('decoder') as dscope:
            decoder_real = self.build_decoder(z, args.latent_size, reuse=(gpu_id>0))
            decoder_fake = self.build_decoder(samples, args.latent_size, reuse=True)
        # losses
        self.build_losses(x, z_mean, z_stddev, decoder_real)

        return (encoder, z, z_mean, z_stddev, decoder_real, decoder_fake)
        

    def build_losses(self, x, z_mean, z_stddev, decoder):
        with tf.variable_scope('losses/decoder'):
            d_loss = tf.negative(tf.reduce_sum(x * tf.log(1e-8 + decoder) + \
                                                   (1 - x) * tf.log(1e-8 + (1 - decoder))), name='decoder_loss')
        with tf.variable_scope('losses/latent'):
            # reparam trick
            l_loss = tf.multiply(0.5, tf.reduce_sum(tf.square(z_mean) + \
                                                        tf.square(z_stddev) - \
                                                        tf.log(1e-8 + tf.square(z_stddev)) - 1), name='latent_loss')
        with tf.variable_scope('losses/total'):
            t_loss = tf.reduce_mean(d_loss + l_loss, name='total_loss')

        for l in [d_loss, l_loss, t_loss]:
            tf.add_to_collection('losses', l)
        
        
    def build_encoder(self, x, reuse=False):
        """Input: 64x64x3. Output: 4x4x32."""
        # layer_sizes = [64, 128, 256, 256, 96, 32] 
        with tf.variable_scope('conv1'):
            x = lrelu(conv2d(x, 3, 64, 5, 2, reuse=reuse, name='c1'))
            self.activation_summary(x)
            
        with tf.variable_scope('conv2'):
            x = lrelu(conv2d(x, 64, 128, 5, 2, reuse=reuse, name='c2'))
            self.activation_summary(x)
            
        with tf.variable_scope('conv3'):
            x = lrelu(conv2d(x, 128, 256, 5, 2, reuse=reuse, name='c3'))
            self.activation_summary(x)
            
        with tf.variable_scope('conv4'):
            x = lrelu(conv2d(x, 256, 256, 5, 2, reuse=reuse, name='c4'))
            self.activation_summary(x)
            
        with tf.variable_scope('conv5'):
            x = lrelu(conv2d(x, 256, 96, 1, reuse=reuse, name='c5'))
            self.activation_summary(x)
            
        with tf.variable_scope('conv6'):
            x = lrelu(conv2d(x, 96, 32, 1, reuse=reuse, name='c6'))
            self.activation_summary(x)
            
        tf.identity(x, name='sample')
        return x

    
    def build_latent(self, x, batch_size, latent_size, reuse=False):
        """Input: 4x4x32. Output: 200."""
        with tf.variable_scope('flatten'):
            flat = flatten(x)
            
        with tf.variable_scope('mean'):
            z_mean = dense(flat, 32*4*4, latent_size, reuse=reuse, name='d1')
            self.activation_summary(z_mean)
            
        with tf.variable_scope('stddev'):
            z_stddev = dense(flat, 32*4*4, latent_size, reuse=reuse, name='d2')
            self.activation_summary(z_stddev)
            
        with tf.variable_scope('gaussian'):
            samples = tf.random_normal([batch_size, latent_size], 0, 1, dtype=tf.float32)
            self.activation_summary(samples)
            z = (z_mean + (z_stddev * samples))
            self.activation_summary(z)
            
        tf.identity(z, name='sample')
        return (samples, z, z_mean, z_stddev)

    
    def build_decoder(self, x, latent_size, reuse=False):
        """Input: 200. Output: 64x64x3."""
        # layer sizes = [96, 256, 256, 128, 64]
        with tf.variable_scope('dense'):
            x = dense(x, latent_size, 32*4*4, reuse=reuse, name='d1')
            self.activation_summary(x)
            
        with tf.variable_scope('conv1'):
            x = tf.reshape(x, [-1, 4, 4, 32]) # un-flatten
            x = tf.nn.relu(conv2d(x, 32, 96, 1, reuse=reuse, name='c1'))
            self.activation_summary(x)
            
        with tf.variable_scope('conv2'):
            x = tf.nn.relu(conv2d(x, 96, 256, 1, reuse=reuse, name='c2'))
            self.activation_summary(x)
            
        with tf.variable_scope('deconv1'):
            x = tf.nn.relu(deconv2d(x, 256, 256, 5, 2, reuse=reuse, name='dc1'))
            self.activation_summary(x)
            
        with tf.variable_scope('deconv2'):
            x = tf.nn.relu(deconv2d(x, 256, 128, 5, 2, reuse=reuse, name='dc2'))
            self.activation_summary(x)
            
        with tf.variable_scope('deconv3'):
            x = tf.nn.relu(deconv2d(x, 128, 64, 5, 2, reuse=reuse, name='dc3'))
            self.activation_summary(x)
            
        with tf.variable_scope('deconv4'):
            x = tf.nn.sigmoid(deconv2d(x, 64, 3, 5, 2, reuse=reuse, name='dc4'))
            self.activation_summary(x)
            
        tf.identity(x, name='sample')
        return x


