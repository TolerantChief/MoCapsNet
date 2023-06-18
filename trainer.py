########################################
#### Licensed under the MIT license ####
########################################

import torch
import torch.nn as nn
import torch.optim as optim
import os
from numpy import prod
from datetime import datetime
from model import CapsuleNetwork
from loss import CapsuleLoss
from time import time
from torchbearer.callbacks import EarlyStopping

from ranger21 import Ranger21

from conflicting_bundles import bundle_entropy
from momentumnet import transform_to_momentumnet
import matplotlib.pyplot as plt
from mem_profile import get_gpu_memory_map

SAVE_MODEL_PATH = 'checkpoints/'
if not os.path.exists(SAVE_MODEL_PATH):
    os.mkdir(SAVE_MODEL_PATH)


class CapsNetTrainer:
    """
    Wrapper object for handling training and evaluation
    """

    def __init__(self, loaders, args, device):
        self.device = device
        self.multi_gpu = args.multi_gpu
        self.batch_size = args.batch_size
        self.cb_batch_size = args.cb_batch_size
        self.conflicts = args.conflicts
        self.modelname = args.modelname

        self.loaders = loaders
        img_shape = self.loaders['train'].dataset[0][0].numpy().shape

        self.net = CapsuleNetwork(args, img_shape=img_shape, channels=256, primary_dim=8, num_classes=args.num_classes,
                                  out_dim=16, device=self.device).to(self.device)

        # print(self.net)
        if args.momentum:
            self.net = transform_to_momentumnet(
                self.net,
                ["blocks." + str(i) +
                 ".functions" for i in range(args.num_res_blocks)],
                gamma=args.gamma,
                use_backprop=False,
                is_residual=True,
                keep_first_layer=False,
            )

        if self.multi_gpu:
            self.net = nn.DataParallel(self.net)

        self.criterion = CapsuleLoss(loss_lambda=0.5, recon_loss_scale=5e-4)

        if args.optimizer == 'ranger21':
            print('using ranger21 optimizer...')
            self.optimizer = Ranger21(
                self.net.parameters(), lr=args.learning_rate, num_epochs=args.epochs, num_batches_per_epoch=args.batch_size)
        else:
            print('using adam optimizer...')
            self.optimizer = optim.Adam(
                self.net.parameters(), lr=args.learning_rate)
            self.scheduler = optim.lr_scheduler.ExponentialLR(
                self.optimizer, gamma=args.lr_decay)
        print(8*'#', 'PyTorch Model built'.upper(), 8*'#')
        print('Num params:', sum([prod(p.size())
              for p in self.net.parameters()]))

    def __repr__(self):
        return repr(self.net)

    def run(self, epochs, classes):
        print(8*'#', 'Run started'.upper(), 8*'#')
        print('MODE;EPOCH;LOSS:ACCURACY;TIME')
        eye = torch.eye(len(classes)).to(self.device)
        max_memory_usage = 0

        early_stopping = EarlyStopping(patience=10)
        best_test_acc = 0
        best_epoch = 0
        epochs_no_improve = 0
        
        history = {'train_loss': [], 'train_acc': [], 'test_loss': [], 'test_acc': []}

        for epoch in range(1, epochs+1):
            for phase in ['train', 'test']:
                if phase == 'train':
                    self.net.train()
                else:
                    self.net.eval()

                t0 = time()
                running_loss = 0.0
                correct = 0
                total = 0
                for i, (images, labels) in enumerate(self.loaders[phase]):
                    t1 = time()
                    images, labels = images.to(
                        self.device), labels.to(self.device)
                    # One-hot encode labels
                    labels = eye[labels]

                    self.optimizer.zero_grad()

                    outputs, reconstructions, layers = self.net(images)
                    loss = self.criterion(
                        outputs, labels, images, reconstructions)

                    if phase == 'train':
                        loss.backward()
                        self.optimizer.step()

                    running_loss += loss.item()

                    _, predicted = torch.max(outputs, 1)
                    _, labels = torch.max(labels, 1)
                    total += labels.size(0)
                    correct += (predicted == labels).sum()
                    accuracy = float(correct) / float(total)


                    # measure conflicting bundles
                    if i == 0 and self.conflicts:
                        cb_batch_size = min(
                            self.cb_batch_size, self.batch_size)
                        # For each layer
                        for i, l in enumerate(layers):
                            a_batch = l[:cb_batch_size]
                            nb, be = bundle_entropy(
                                a_batch, labels, num_classes=len(classes))
                            print("Layer %d | Bundle entropy %.3f" % (i, be))
                            print("Layer %d | Number of bundles %d" % (i, nb))

                if phase == 'train':
                    history['train_loss'].append(running_loss/(i+i))
                    history['train_acc'].append(accuracy)
                if phase == 'test':
                    history['test_loss'].append(running_loss/(i+i))
                    history['test_acc'].append(accuracy)
                    if accuracy > best_test_acc:
                        best_test_acc = accuracy
                        best_epoch = epoch
                        epochs_no_improve = 0
                    else:
                        epochs_no_improve +=1 
                        if epochs_no_improve == early_stopping.patience:
                            print('Early Stopping')
                            print(f'The best test accuracy was {best_test_acc} in epoch {best_epoch}')
                            
                            now = str(datetime.now()).replace(" ", "-")
                            error_rate = round((1-accuracy)*100, 2)
                            torch.save(self.net.state_dict(), os.path.join(
                                SAVE_MODEL_PATH, f'{error_rate}_{now}.pth.tar'))
                    
                            class_correct = list(0. for _ in classes)
                            class_total = list(0. for _ in classes)
                            for images, labels in self.loaders['test']:
                                images, labels = images.to(self.device), labels.to(self.device)
                                outputs, reconstructions, _ = self.net(images)
                                _, predicted = torch.max(outputs, 1)
                                c = (predicted == labels).squeeze()
                                for i in range(labels.size(0)):
                                    label = labels[i]
                                    class_correct[label] += c[i].item()
                                    class_total[label] += 1
                    
                            print(8*'#', 'Run ended'.upper(), 8*'#')
                    
                            print('Accuracy of ', self.modelname)
                            for i in range(len(classes)):
                                print('Accuracy of %5s : %2d %%' % (
                                    classes[i], 100 * class_correct[i] / class_total[i]))
                    
                            print('max. memory usage: ', max_memory_usage)
                        
                            epochs = range(1, epoch + 1)
                            
                            plt.figure()
                            
                            plt.subplot(2, 1, 1)
                            
                            plt.plot(epochs, history['train_acc'], label='Training Accuracy')
                            plt.plot(epochs, history['test_acc'], label='Validation Accuracy')
                            plt.xlabel('Epochs')
                            plt.ylabel('Accuracy')
                            plt.title('Training and Validation Accuracy')
                            plt.legend()
                            
                            plt.subplot(2,1,2)
                            
                            plt.plot(epochs, history['train_loss'], label='Training Loss')
                            plt.plot(epochs, history['test_loss'], label='Validation Loss')
                            plt.xlabel('Epochs')
                            plt.ylabel('Loss')
                            plt.title('Training and Validation Loss')
                            plt.legend()
                            
                            plt.tight_layout()
                            # plt.show()    

                            plt.savefig('loss_accuracy.png')
                            
                            return
                print(
                    f'{phase.upper()};{epoch};{running_loss/(i+1)};{accuracy};{round(time()-t0, 3)}s')

                # check memory usage
                current_memory_usage = get_gpu_memory_map()[0]
                if current_memory_usage > max_memory_usage:
                    max_memory_usage = current_memory_usage

            if hasattr(self, 'scheduler'):
                self.scheduler.step()

        now = str(datetime.now()).replace(" ", "-")
        error_rate = round((1-accuracy)*100, 2)
        torch.save(self.net.state_dict(), os.path.join(
            SAVE_MODEL_PATH, f'{error_rate}_{now}.pth.tar'))

        class_correct = list(0. for _ in classes)
        class_total = list(0. for _ in classes)
        for images, labels in self.loaders['test']:
            images, labels = images.to(self.device), labels.to(self.device)
            outputs, reconstructions, _ = self.net(images)
            _, predicted = torch.max(outputs, 1)
            c = (predicted == labels).squeeze()
            for i in range(labels.size(0)):
                label = labels[i]
                class_correct[label] += c[i].item()
                class_total[label] += 1

        print(8*'#', 'Run ended'.upper(), 8*'#')

        print('Accuracy of ', self.modelname)
        for i in range(len(classes)):
            print('Accuracy of %5s : %2d %%' % (
                classes[i], 100 * class_correct[i] / class_total[i]))

        print('max. memory usage: ', max_memory_usage)
