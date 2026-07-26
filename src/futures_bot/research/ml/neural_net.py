"""A small, fixed MLP architecture for the Neural Network model option.

Fixed on purpose: reloading a PyTorch model by pickling an arbitrary
`nn.Module` subclass is fragile across code changes (the class definition
has to still exist, unchanged, at load time). Persisting only a
`state_dict` plus this one known architecture function means
`predict.load_model` never needs to reload arbitrary code -- just
`input_dim` (== the trained model's `feature_columns` count) and
`hidden_sizes` (stored in `hyperparameters`), both already on the model's
row.
"""

from __future__ import annotations


def build_mlp(input_dim: int, hidden_sizes: tuple = (32, 16)):
    import torch.nn as nn

    layers: list = []
    prev = input_dim
    for size in hidden_sizes:
        layers.append(nn.Linear(prev, size))
        layers.append(nn.ReLU())
        prev = size
    layers.append(nn.Linear(prev, 1))
    layers.append(nn.Sigmoid())
    return nn.Sequential(*layers)
