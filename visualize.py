import tensorflow as tf
import numpy as np
import argparse
import os
import cv2
import datetime
from math import ceil, sqrt
from itertools import chain
# 
from models import simple_fc, simple_cnn
from util import *








def reload_session(dir, fn=None):
    tf.reset_default_graph()
    sess = tf.Session()
    saver = tf.train.import_meta_graph(os.path.join(dir, 'model'))
    if fn is None:
        chk_file = tf.train.latest_checkpoint(os.path.join(dir, 'checkpoints'))
    else:
        chk_file = fn
    # print('latest checkpoint:', latest)
    saver.restore(sess, chk_file)
    return sess



# usage: list(chunks(some_list, chunk_size)) ==> list of lists of that size
def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]



# TODO: Add option to use color for a border
def stitch_montage(image_list, add_border=True, use_width=0):
    """Stitch a list of equally-shaped images into a single image."""
    num_images = len(image_list)
    if use_width > 0:
        montage_w = use_width
    else:
        montage_w = ceil(sqrt(num_images))
    montage_h = int(num_images/montage_w)
    ishape = image_list[0].shape
    # black borders
    v_border = np.zeros((ishape[0], 1, ishape[-1]))
    h_border = np.zeros((1, (ishape[1]+1) * montage_w + 1, ishape[-1]))

    montage = list(chunks(image_list, montage_w))
    # fill in any remaining missing images in square montage
    remaining = montage_w - (num_images - montage_w * montage_h)
    if remaining < montage_w:
        # dummy_shape = weights[:,:,:,0].shape if rgb else np.expand_dims(weights[:,:,0,0], 2).shape
        for _ in range(remaining):
            montage[-1].append(np.zeros(ishape))

    if add_border:
        b = [v_border for x in range(len(montage[0]))]
        c = [h_border for x in range(len(montage))]        
        return np.concatenate(list(chain(*zip(c, [np.concatenate(list(chain(*zip(b, row))) + [v_border], axis=1) for row in montage]))) + [h_border], axis=0)
    else:
        return np.concatenate([np.concatenate(row, axis=1) for row in montage], axis=0)
    

# TODO: Add option for x_input, or find it dynamically
def visualize_activations(layer, input):
    """Generate image of layer's activations given a specific input."""
    # input is a single image, so put it into batch form for sess.run
    input = np.expand_dims(input, 0)
    graph = tf.get_default_graph()
    x_input = graph.as_graph_element('inputs/x_input').outputs[0]
    activations = sess.run(layer, feed_dict={x_input: input})

    image_list = []
    for f_idx in range(activations.shape[-1]):
        f = activations[0,:,:,f_idx] * 255.0
        image_list.append(np.expand_dims(f, 2))
    return stitch_montage(image_list)


def visualize_all_activations(layers, input):
    return [visualize_activations(layer, input) for layer in layers]

# # how to use:
# data = get_dataset('floorplan')
# layers = tf.get_collection('layers')
# results = visualize_all_activations(layers, data.test.images[0])
# for i in range(len(results)):
#     cv2.imwrite('result_layer_' + str(i) + '.png', results[i])



# TODO: Given a layer, find the weights
# visualize trained weights
def visualize_weights(var):
    """Generate image of the weights of a layer."""
    weights = sess.run(var)
    rgb = weights.shape[-2] == 3 # should output be rgb or grayscale?
    num_filters = weights.shape[-1] if rgb else weights.shape[-1] * weights.shape[-2]

    image_list = []
    for f_idx in range(weights.shape[-1]):
        if rgb:
            image_list.append(weights[:,:,:,f_idx]*255.0)
        else:
            for f_idx2 in range(weights.shape[-2]):
                f = weights[:,:,f_idx2,f_idx] * 255.0
                image_list.append(np.expand_dims(f, 2))
    return stitch_montage(image_list)


def visualize_all_weights(weights):
    return [visualize_weights(var) for var in weights]

# # how to use:
# # note, the only variables we are interested in are the conv weights, which have shape of length 4
# weight_vars = [v for v in tf.trainable_variables() if len(v.get_shape()) == 4]
# results = visualize_all_weights(weight_vars)
# for i in range(len(results)):
#     print('results', i, results[i].shape)
#     cv2.imwrite('weights_' + str(i) + '.png', results[i])



def visualize_timelapse(workspace_dir, example_images):
    # get list of checkpoint files in order
    # TODO: use creation time instead of name to order
    checkpoint_files = []
    for f in os.listdir(os.path.join(workspace_dir, 'checkpoints')):
        if f.endswith('.meta'):
            checkpoint_files.append(f[:f.rfind('.')])
    checkpoint_files.sort()

    montage = [x*255.0 for x in example_images]
    for f in checkpoint_files:
        sess = reload_session(workspace_dir, os.path.join(workspace_dir, 'checkpoints', f))
        # TODO: find y_hat and x_input dynamically from model
        graph = tf.get_default_graph()
        x_input = graph.as_graph_element('inputs/x_input').outputs[0]
        y_hat = graph.as_graph_element('outputs/decoder/Layer.Decoder.3').outputs[0]
        results = sess.run(y_hat, feed_dict={x_input: example_images})
        for r in results:
            montage.append(r * 255.0)
    return stitch_montage(montage, use_width=len(example_images)) #args.examples)



# util function to convert a tensor into a valid image
def deprocess_image(x):
    # normalize tensor: center on 0., ensure std is 0.1
    x -= x.mean()
    x /= (x.std() + 1e-5)
    x *= 0.1

    # clip to [0, 1]
    x += 0.5
    x = np.clip(x, 0, 1)

    # convert to RGB array
    x *= 255
    # x = x.transpose((1, 2, 0))
    x = np.clip(x, 0, 255).astype('uint8')
    return x


# visualize image that most activates a filter via gradient ascent
def visualize_bestfit_image(layer):
    """Use gradient ascent to find image that best activates a layer's filters."""
    graph = tf.get_default_graph()
    x_input = graph.as_graph_element('inputs/x_input').outputs[0]
    dt = datetime.datetime.now()

    image_list = []
    for idx in range(layer.get_shape()[-1]):
        with tf.device("/gpu:0"):
            dt_f = datetime.datetime.now()
            # start with noise averaged around gray
            input_img_data = np.random.random([1, 64, 64, 3])
            input_img_data = (input_img_data - 0.5) * 20 + 128.0
                
            # build loss and gradients for this filter
            loss = tf.reduce_mean(layer[:,:,:,idx])
            grads = tf.gradients(loss, x_input)[0]
            # normalize gradients
            grads = grads / (tf.sqrt(tf.reduce_mean(tf.square(grads))) + 1e-5)

            for n in range(20):
                loss_value, grads_value = sess.run([loss, grads], feed_dict={x_input: input_img_data})
                input_img_data += grads_value                
                if loss_value <= 0:
                    input_img_data = np.ones([1, 64, 64, 3])
                    break

            # image_list.append(input_img_data[0])
            image_list.append(deprocess_image(input_img_data[0]))
            # print('Completed filter {}/{}, {} elapsed'.format(idx, layer.get_shape()[-1], datetime.datetime.now() - dt_f))
            # tf.reset_default_graph()
            
    # print('Finished', layer)
    # print('Elapsed: {}'.format(datetime.datetime.now() - dt))
    return stitch_montage(image_list) #, add_border=True)
    

def visualize_all_bestfit_images(layers):
    return [visualize_bestfit_image(layer) for layer in layers]


# sess = reload_session()
# layers = tf.get_collection('layers')
# # how to use:
# i = 0
# for i in range(len(layers)):
#     sess = reload_session()
#     layer = tf.get_collection('layers')[i]
#     cv2.imwrite('test_{}.png'.format(i), visualize_bestfit_image(layer))
#     i += 1

    
# sess = reload_session(args.dir)
# layer = tf.get_collection('layers')[0]
# result = visualize_bestfit_image(layer)
# cv2.imwrite('test.png', result)


# layers = tf.get_collection('layers')
# results = visualize_all_bestfit_images(layers)
# for i in range(len(results)):
#     cv2.imwrite('bestfit_' + str(i) + '.png', results[i])


    
# encoder 256: 18.5 min
# encoder 256x2: 37.43 min
# encoder 96: 20.5 min
# encoder 32: 8 min
# decoder 96: 27min, 11 sec
# decoder 256: 1hr 41min

# encoder:
# 64:        37s
# 128:    2m 48s
# 256:   11m 50s
# 256x2: 15m 22s
# 96:     4m 23s

# feature vec:
# 32:     1m 18s

# decoder:
# 96:     5m 46s
# 256:   37m       (roughly)
# 256x2: 31m 4s
# 128:   10m 37s
# 64:    4m 5s
# 3:     8s

# total time: about 122m  (2hr)



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dir', type=str)
    parser.add_argument('--data', type=str)
    parser.add_argument('--examples', type=int)
    args = parser.parse_args()

    data = get_dataset(args.data)
    sample_indexes = np.random.choice(data.test.images.shape[0], args.examples, replace=False)
    example_images = data.test.images[sample_indexes, :]

    ################################################    
    # prep dirs
    
    # example images and activations
    for i in range(args.examples):
        example = example_images[i]
        sample_num = sample_indexes[i]
        example_path = os.path.join(args.dir, 'images', 'activations', 'example' + str(i))
        if not os.path.exists(example_path):
            os.makedirs(example_path)
        image_path = os.path.join(example_path, 'original_' + str(sample_num) + '.png')
        cv2.imwrite(image_path, example * 255.0)
    # weights
    weight_path = os.path.join(args.dir, 'images', 'weights')
    if not os.path.exists(weight_path):
        os.makedirs(weight_path)
    # best fit
    best_path = os.path.join(args.dir, 'images', 'bestfit')
    if not os.path.exists(best_path):
        os.makedirs(best_path)
    # timelapse
    timelapse_path = os.path.join(args.dir, 'images', 'timelapse')
    if not os.path.exists(timelapse_path):
        os.makedirs(timelapse_path)


    ################################################
    # generate images!

    
    # activations
    # TODO: add timelapse for each checkpoint    
    sess = reload_session(args.dir)
    layers = tf.get_collection('layers')
    for n in range(args.examples):
        example = example_images[n]
        example_path = os.path.join(args.dir, 'images', 'activations', 'example' + str(n))
        results = visualize_all_activations(layers, example)
        for i in range(len(results)):
            image_path = os.path.join(example_path, 'activation_layer_' + str(i) + '.png')
            cv2.imwrite(image_path, results[i])


    # example timelapse
    results = visualize_timelapse(args.dir, example_images)
    cv2.imwrite(os.path.join(args.dir, 'images', 'timelapse.png'), results)
    # reset session to most recent checkpoint
    sess = reload_session(args.dir)
    


    # weights
    # TODO: add timelapse for each checkpoint                
    # note, the only variables we are interested in are the conv weights, which have shape of length 4
    weight_vars = [v for v in tf.trainable_variables() if len(v.get_shape()) == 4]
    results = visualize_all_weights(weight_vars)
    for i in range(len(results)):
        weight_path = os.path.join(args.dir, 'images', 'weights', 'weights_' + str(i) + '.png')
        cv2.imwrite(weight_path, results[i])
        
    # best fit via gradient ascent
    sess = reload_session(args.dir)
    layers = tf.get_collection('layers')
    i = 0
    for i in range(1):
    # for i in range(len(layers)):
        sess = reload_session(args.dir)
        layer = tf.get_collection('layers')[i]
        results = visualize_bestfit_image(layer)
        img_path = os.path.join(args.dir, 'images', 'bestfit', 'bestfit_layer_' + str(i) + '.png')
        cv2.imwrite(img_path, results)

