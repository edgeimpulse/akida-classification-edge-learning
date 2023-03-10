##########################################################################################
# Note: This file is used in expert mode and is therefore visible to the user.
# Comments are automatically stripped out unless they start with "#!".
#########################################################################################
import math, random
import tensorflow as tf
from tensorflow.keras.layers import Activation, Dropout, Flatten, Reshape
from tensorflow.keras.optimizers import Adam

from keras import Model
from keras.layers import Activation, Dropout, Reshape, Flatten

from akida_models.layer_blocks import dense_block
from akida_models import akidanet_imagenet

from ei_tensorflow import training
import cnn2snn

BATCH_SIZE = 32

#! Implements the data augmentation policy
def augmentation_function(input_shape: tuple):
    def augment_image(image, label):
        # Flips the image randomly
        image = tf.image.random_flip_left_right(image)

        #! Increase the image size, then randomly crop it down to
        #! the original dimensions
        resize_factor = random.uniform(1, 1.2)
        new_height = math.floor(resize_factor * input_shape[0])
        new_width = math.floor(resize_factor * input_shape[1])
        image = tf.image.resize_with_crop_or_pad(image, new_height, new_width)
        image = tf.image.random_crop(image, size=input_shape)

        #! Vary the brightness of the image
        image = tf.image.random_brightness(image, max_delta=0.2)

        return image, label

    return augment_image

def train(train_dataset: tf.data.Dataset,
          validation_dataset: tf.data.Dataset,
          num_classes: int, pretrained_weights: str,
          input_shape: tuple, learning_rate: int, epochs: int,
          dense_layer_neurons: int, dropout: float,
          data_augmentation: bool, callbacks,
          best_model_path: str, quantize_function):
    #! Create a quantized base model without top layers
    base_model = akidanet_imagenet(input_shape=input_shape,
                                classes=num_classes,
                                alpha=0.5,
                                include_top=False,
                                input_scaling=None,
                                pooling='avg',
                                weight_quantization=4,
                                activ_quantization=4,
                                input_weight_quantization=8)

    #! Get pretrained quantized weights and load them into the base model
    #! Available base models are:
    #! akidanet_imagenet_224_alpha_50_iq8_wq4_aq4.h5 - quantized model (8/4/4), 224x224x3, alpha=0.5
    #! akidanet_imagenet_224_alpha_50.h5             - float32 model, 224x224x3, alpha=0.5
    #! akidanet_imagenet_160_alpha_50_iq8_wq4_aq4.h5 - quantized model (8/4/4), 160x160x3, alpha=0.5
    #! akidanet_imagenet_160_alpha_50.h5             - float32 model, 160x160x3, alpha=0.5
    base_model.load_weights(pretrained_weights, by_name=True, skip_mismatch=True)
    base_model.trainable = False

    output_model = base_model.output
    output_model = Flatten()(output_model)
    if dense_layer_neurons > 0:
        output_model = dense_block(output_model,
                                units=dense_layer_neurons,
                                add_batchnorm=False,
                                add_activation=True)
    if dropout > 0:
        output_model = Dropout(dropout)(output_model)
    output_model = dense_block(output_model,
                            units=num_classes,
                            add_batchnorm=False,
                            add_activation=False)
    output_model = Activation('softmax')(output_model)
    output_model = Reshape((num_classes,))(output_model)

    #! Build the model
    model = Model(base_model.input, output_model)

    opt = Adam(learning_rate=learning_rate)
    model.compile(optimizer=opt, loss='categorical_crossentropy', metrics=['accuracy'])

    if data_augmentation:
        train_dataset = train_dataset.map(augmentation_function(input_shape),
                                          num_parallel_calls=tf.data.AUTOTUNE)

    #! This controls the batch size, or you can manipulate the tf.data.Dataset objects yourself
    train_dataset = train_dataset.batch(BATCH_SIZE, drop_remainder=False)
    validation_dataset = validation_dataset.batch(BATCH_SIZE, drop_remainder=False)

    #! Train the neural network
    model.fit(train_dataset, epochs=epochs, validation_data=validation_dataset, verbose=2, callbacks=callbacks)

    print('')
    print('Initial training done.', flush=True)
    print('')

    akida_model =  quantize_function(model=model,
                                     train_dataset=train_dataset,
                                     validation_dataset=validation_dataset,
                                     optimizer=opt,
                                     fine_tune_loss='categorical_crossentropy',
                                     fine_tune_metrics=['accuracy'],
                                     best_model_path=best_model_path,
                                     callbacks=callbacks)

    return model, akida_model