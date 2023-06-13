import os
import torch
import torchvision
import torchvision.transforms as transforms
from trainer import CapsNetTrainer
import argparse

#torch.manual_seed(23545)

DATA_PATH = '/content/data'

# Collect arguments (if any)
parser = argparse.ArgumentParser()

parser.add_argument('--dataset', type=str, default='MNIST',
                    help="'MNIST', 'SVHN' or 'CIFAR' (case insensitive).")
# Batch size
parser.add_argument('-bs', '--batch_size', type=int,
                    default=128, help='Batch size.')
# Epochs
parser.add_argument('-e', '--epochs', type=int,
                    default=30, help='Number of epochs.')
# Learning rate
parser.add_argument('-lr', '--learning_rate', type=float,
                    default=1e-3, help='Learning rate.')
# Number of routing iterations
parser.add_argument('--num_routing', type=int, default=3,
                    help='Number of routing iteration in routing capsules.')

# routing algorithm
parser.add_argument('--routing', type=str, default="RBA",
                    help='RBA or SDA routing.')                    

# Exponential learning rate decay
parser.add_argument('--lr_decay', type=float, default=0.96,
                    help='Exponential learning rate decay.')
# Select device "cuda" for GPU or "cpu"
parser.add_argument('--device', type=str, default=("cuda" if torch.cuda.is_available() else "cpu"),
                    choices=['cuda', 'cpu'], help='Device to use. Choose "cuda" for GPU or "cpu".')
# Use multiple GPUs?
parser.add_argument('--multi_gpu', action='store_true',
                    help='Flag whether to use multiple GPUs.')
# Select GPU device
parser.add_argument('--gpu_device', type=int, default=None,
                    help='ID of a GPU to use when multiple GPUs are available.')
# Data directory
parser.add_argument('--data_path', type=str, default=DATA_PATH,
                    help='Path to the MNIST or CIFAR dataset. Alternatively you can set the path as an environmental variable $data.')

# use residual learning
parser.add_argument('-res', '--residual', dest='residual', action='store_true',
                    help='Use residual shortcut connections.')

# measure conflicting bundles
parser.add_argument('-cb', '--conflicts', dest='conflicts', action='store_true',
                    help='Measure conflicting bundles.')

# conflicting bundles batch size
parser.add_argument('-cb_bs', '--cb_batch_size', type=int,
                    default=32, help='Batch size of conflicting bundles.')

# use momentum
parser.add_argument('-m', '--momentum', dest='momentum', action='store_true',
                    help='Use residual shortcut connections..')
parser.add_argument('-g', '--gamma', type=float,
                    default=0.9, help='Momentum term.')

parser.add_argument('-b', '--num_res_blocks', type=int,
                    default=1, help='Number of residual blocks.')

parser.add_argument('-c', '--num_caps', type=int,
                    default=32, help='Number of capsules.')

# optimizer
parser.add_argument('-o', '--optimizer', type=str,
                    default='adam', help='One of: ranger21, adam')

args = parser.parse_args()

if not args.residual and not args.momentum:
    args.modelname = "CapsNet_" + str(args.num_res_blocks)
elif args.residual and not args.momentum:
    args.modelname = "ResCapsNet_" + str(args.num_res_blocks)
elif args.residual and args.momentum:
    args.modelname = "MoCapsNet_" + str(args.num_res_blocks)

device = torch.device(args.device)

if args.gpu_device is not None:
    torch.cuda.set_device(args.gpu_device)

if args.multi_gpu:
    args.batch_size *= torch.cuda.device_count()

datasets = {
    'MNIST': torchvision.datasets.MNIST,
    'CIFAR': torchvision.datasets.CIFAR10,
    'CIFAR100': torchvision.datasets.CIFAR100,
    'SVHN': torchvision.datasets.SVHN,
}

# dataset defaults
split_train = {'train': True}
split_test = {'train': False}
size = 32
mean, std = ((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))

if args.dataset.upper() == 'MNIST':
    args.data_path = os.path.join(args.data_path, 'MNIST')
    size = 28
    classes = list(range(10))
    mean, std = ((0.1307,), (0.3081,))
elif args.dataset.upper() == 'CIFAR':
    args.data_path = os.path.join(args.data_path, 'CIFAR')
    classes = ['plane', 'car', 'bird', 'cat', 'deer',
               'dog', 'frog', 'horse', 'ship', 'truck']
elif args.dataset.upper() == 'CIFAR100':
    args.data_path = os.path.join(args.data_path, 'CIFAR100')
    classes = list(range(100))
elif args.dataset.upper() == 'SVHN':
    args.data_path = os.path.join(args.data_path, 'SVHN')
    classes = list(range(10))
    split_train = {'split': "train"}
    split_test = {'split': "test"}
elif args.dataset.upper() == 'JAMONES':
	args.data_path = os.path.join(args.data_path, 'JAMONES_CROPPED')
	classes = list(range(26))
	size = 50
	split_train = {'split': "train"}
	split_test = {'split': "test"}
else:
    raise ValueError('Dataset must be either MNIST, SVHN or CIFAR')

args.num_classes = len(classes)

transform = transforms.Compose([
    # shift by 2 pixels in either direction with zero padding.
    transforms.Resize((size,size)),
    transforms.RandomHorizontalFlip(),
    transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.1),
    transforms.RandomGrayscale(p=0.1),
    transforms.RandomAffine(degrees=10, translate=(0.1, 0.1), scale=(0.8, 1.2), shear=10),
    transforms.RandomPerspective(distortion_scale=0.1, p=0.5),
    transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 2.0)),
    transforms.RandomRotation(10),
    transforms.RandomCrop(size, padding=2),
    transforms.ToTensor(),
    transforms.Normalize(mean, std)
])
loaders = {}

if args.dataset.upper() not in datasets:
	test_size = 0.2
	dataset = torchvision.datasets.ImageFolder(root=args.data_path, transform=transform)
	num_data = len(dataset)
	num_test = int(test_size * num_data)
	num_train = num_data - num_test
	train_dataset, test_dataset = torch.utils.data.random_split(dataset, [num_train, num_test])
	loaders['train'] = torch.utils.data.DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
	loaders['test'] = torch.utils.data.DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False)
else:	
	trainset = datasets[args.dataset.upper()](
		root=args.data_path, **split_train, download=True, transform=transform)
	loaders['train'] = torch.utils.data.DataLoader(
		trainset, batch_size=args.batch_size, shuffle=True, num_workers=2)

	testset = datasets[args.dataset.upper()](
		root=args.data_path, **split_test, download=True, transform=transform)
	loaders['test'] = torch.utils.data.DataLoader(
		testset, batch_size=args.batch_size, shuffle=False, num_workers=2)

print(8*'#', f'Using {args.dataset.upper()} dataset', 8*'#')

# Run
caps_net = CapsNetTrainer(loaders, args, device=device)
caps_net.run(args.epochs, classes=classes)
