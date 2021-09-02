########################################
#### Licensed under the MIT license ####
########################################

import torch
import torch.nn as nn
import torch.nn.functional as F
from numpy import prod
import capsules as caps
from capsules import squash


class ResCapsBlock(nn.Module):
    def __init__(self, in_dim, in_caps, num_classes, out_dim, num_routing, skip, device):
        super(ResCapsBlock, self).__init__()
        self.skip = skip

        self.functions = nn.Sequential(
            *[
                nn.Sequential(
                    caps.RoutingCapsules(
                        in_dim, in_caps, num_classes, out_dim, num_routing, device=device)
                )
                for _ in range(2)
            ]
        )

    def forward(self, x):
        for f in self.functions:
            if self.skip:
                x = x + f(x)
            else:
                x = f(x)
        return x


class CapsuleNetwork(nn.Module):
    def __init__(self, args, img_shape, channels, primary_dim, num_classes, out_dim, device: torch.device, kernel_size=9):
        super(CapsuleNetwork, self).__init__()
        self.img_shape = img_shape
        self.num_classes = num_classes
        self.device = device

        self.conv1 = nn.Conv2d(
            img_shape[0], channels, kernel_size, stride=1, bias=True)
        self.relu = nn.ReLU(inplace=True)

        self.primary = caps.PrimaryCapsules(
            channels, channels, primary_dim, kernel_size)

        primary_caps = int(channels / primary_dim * (img_shape[1] - 2*(
            kernel_size-1)) * (img_shape[2] - 2*(kernel_size-1)) / 4)

        # caps layer 1
        self.caps1 = caps.RoutingCapsules(
            primary_dim, primary_caps, 32, primary_dim, args.num_routing, device=self.device)

        self.blocks = torch.nn.ModuleList()
        for _ in range(args.num_res_blocks):
            self.blocks.append(ResCapsBlock(
                primary_dim, 32, 32, primary_dim, args.num_routing, args.residual, self.device))

        # caps layer 2
        self.caps2 = caps.RoutingCapsules(
            primary_dim, 32, num_classes, out_dim, args.num_routing, device=self.device)

        self.decoder = nn.Sequential(
            nn.Linear(out_dim * num_classes, 512),
            nn.ReLU(inplace=True),
            nn.Linear(512, 1024),
            nn.ReLU(inplace=True),
            nn.Linear(1024, int(prod(img_shape))),
            nn.Sigmoid()
        )

    def forward(self, x):

        out = self.conv1(x)
        out = self.relu(out)
        out = self.primary(out)
        out = self.caps1(out)

        layers = []
        for block in self.blocks:
            out = block(out)
            layers.append(out)  # for cb measurement

        out = self.caps2(out)
        preds = torch.norm(out, dim=-1)

        # Reconstruct the *predicted* image
        _, max_length_idx = preds.max(dim=1)
        y = torch.eye(self.num_classes).to(self.device)
        y = y.index_select(dim=0, index=max_length_idx).unsqueeze(2)

        reconstructions = self.decoder((out*y).view(out.size(0), -1))
        reconstructions = reconstructions.view(-1, *self.img_shape)

        return preds, reconstructions, layers
