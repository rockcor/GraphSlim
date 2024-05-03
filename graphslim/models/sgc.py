"""multiple transformaiton and multiple propagation"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch_sparse
from torch_sparse import SparseTensor, matmul

from graphslim.models.base import BaseGNN
from graphslim.models.layers import GraphConvolution, MyLinear


class SGC(BaseGNN):

    def __init__(self, nfeat, nhid, nclass, nlayers=2, dropout=0.5, lr=0.01, weight_decay=5e-4,
                 with_relu=True, with_bias=True, with_bn=False, device=None):
        super(SGC, self).__init__(nfeat, nhid, nclass, nlayers, dropout, lr, weight_decay,
                                  with_relu, with_bias, with_bn, device=device)

        self.conv = GraphConvolution(nfeat, nclass, with_bias=with_bias)
        self.nlayers = nlayers

        if not with_relu:
            self.weight_decay = 0
        else:
            self.weight_decay = weight_decay
        self.with_relu = with_relu
        if with_bn:
            print('Warning: SGC does not have bn!!!')

    def forward(self, x, adj, output_layer_features=False):
        weight = self.conv.weight
        bias = self.conv.bias
        x = torch.mm(x, weight)
        for i in range(self.nlayers):
            if isinstance(adj, list) or len(adj.shape) == 3:
                # only synthetic graph use batched adj
                adj = torch.as_tensor(adj)
                x = torch.matmul(adj, x)
            else:
                x = torch.spmm(adj, x)
        x = x + bias
        if self.multi_label:
            return torch.sigmoid(x)
        else:
            return F.log_softmax(x, dim=1)

    def forward_sampler(self, x, adjs):
        weight = self.conv.weight
        bias = self.conv.bias
        x = torch.mm(x, weight)
        for ix, (adj, _, size) in enumerate(adjs):
            x = torch_sparse.matmul(adj, x)
        x = x + bias
        if self.multi_label:
            return torch.sigmoid(x)
        else:
            return F.log_softmax(x, dim=1)

    def forward_syn(self, x, adjs):
        weight = self.conv.weight
        bias = self.conv.bias
        x = torch.mm(x, weight)
        for ix, (adj) in enumerate(adjs):
            if type(adj) == torch.Tensor:
                x = adj @ x
            else:
                x = torch_sparse.matmul(adj, x)
        x = x + bias
        if self.multi_label:
            return torch.sigmoid(x)
        else:
            return F.log_softmax(x, dim=1)

    def initialize(self):
        """Initialize parameters of GCN.
        """
        self.conv.reset_parameters()
        if self.with_bn:
            for bn in self.bns:
                bn.reset_parameters()


class SGCRich(BaseGNN):
    '''
    multiple transformation layers
    '''

    def __init__(self, nfeat, nhid, nclass, nlayers=2, dropout=0.5, lr=0.01, weight_decay=5e-4,
                 ntrans=2, with_relu=True, with_bias=True, with_bn=False, device=None):

        """nlayers indicates the number of propagations"""
        super(SGCRich, self).__init__(nfeat, nhid, nclass, nlayers, dropout, lr, weight_decay,
                                      with_relu, with_bias, with_bn, device=device)
        self.nlayers = nlayers
        self.layers = nn.ModuleList([])
        if ntrans == 1:
            self.layers.append(MyLinear(nfeat, nclass))
        else:
            self.layers.append(MyLinear(nfeat, nhid))
            if with_bn:
                self.bns = torch.nn.ModuleList()
                self.bns.append(nn.BatchNorm1d(nhid))
            for i in range(ntrans - 2):
                if with_bn:
                    self.bns.append(nn.BatchNorm1d(nhid))
                self.layers.append(MyLinear(nhid, nhid))
            self.layers.append(MyLinear(nhid, nclass))

    def forward(self, x, adj, output_layer_features=False):
        for ix, layer in enumerate(self.layers):
            x = layer(x)
            if ix != len(self.layers) - 1:
                x = self.bns[ix](x) if self.with_bn else x
                x = F.relu(x)
                x = F.dropout(x, self.dropout, training=self.training)

        for i in range(self.nlayers):
            x = adj @ x

        x = x.reshape(-1, x.shape[-1])
        return F.log_softmax(x, dim=1)

    def forward_sampler(self, x, adjs):
        for ix, layer in enumerate(self.layers):
            x = layer(x)
            if ix != len(self.layers) - 1:
                x = self.bns[ix](x) if self.with_bn else x
                x = F.relu(x)
                x = F.dropout(x, self.dropout, training=self.training)

        for ix, (adj, _, size) in enumerate(adjs):
            x = torch_sparse.matmul(adj, x)

        if self.multi_label:
            return torch.sigmoid(x)
        else:
            return F.log_softmax(x, dim=1)

    def forward_syn(self, x, adjs):
        for ix, layer in enumerate(self.layers):
            x = layer(x)
            if ix != len(self.layers) - 1:
                x = self.bns[ix](x) if self.with_bn else x
                x = F.relu(x)
                x = F.dropout(x, self.dropout, training=self.training)

        for ix, (adj) in enumerate(adjs):
            if type(adj) == torch.Tensor:
                x = adj @ x
            else:
                x = torch_sparse.matmul(adj, x)

        if self.multi_label:
            return torch.sigmoid(x)
        else:
            return F.log_softmax(x, dim=1)

    #
    # def fit_with_val(self, features, adj, labels, data, train_iters=200, initialize=True, verbose=False, normalize=True,
    #                  patience=None, val=False, **kwargs):
    #     '''data: full data class'''
    #     if initialize:
    #         self.initialize()
    #
    #     # features, adj, labels = data.feat_train, data.adj_train, data.labels_train
    #     if type(adj) is not torch.Tensor:
    #         features, adj, labels = utils.to_tensor(features, adj, labels, device=self.device)
    #     else:
    #         features = features.to(self.device)
    #         adj = adj.to(self.device)
    #         labels = labels.to(self.device)
    #
    #     if normalize:
    #         if utils.is_sparse_tensor(adj):
    #             adj_norm = utils.normalize_adj_tensor(adj, sparse=True)
    #         else:
    #             adj_norm = utils.normalize_adj_tensor(adj)
    #     else:
    #         adj_norm = adj
    #
    #     if 'feat_norm' in kwargs and kwargs['feat_norm']:
    #         features = utils.row_normalize_tensor(features - features.min())
    #
    #     self.adj_norm = adj_norm
    #     self.features = features
    #
    #     if len(labels.shape) > 1:
    #         self.multi_label = True
    #         self.loss = torch.nn.BCELoss()
    #     else:
    #         self.multi_label = False
    #         self.loss = F.nll_loss
    #
    #     labels = labels.float() if self.multi_label else labels
    #     self.labels = labels
    #
    #     if val:
    #         self._train_with_val(labels, data, train_iters, verbose, adj_val=True)
    #     else:
    #         self._train_with_val(labels, data, train_iters, verbose)
    #
    # def _train_with_val(self, labels, data, train_iters, verbose, adj_val=False):
    #     if adj_val:
    #         feat_full, adj_full = data.feat_val, data.adj_val
    #     else:
    #         feat_full, adj_full = data.feat_full, data.adj_full
    #
    #     feat_full, adj_full = utils.to_tensor(feat_full, adj_full, device=self.device)
    #     adj_full_norm = utils.normalize_adj_tensor(adj_full, sparse=True)
    #     labels_val = torch.LongTensor(data.labels_val).to(self.device)
    #
    #     if verbose:
    #         print('=== training gcn model ===')
    #     optimizer = optim.Adam(self.parameters(), lr=self.lr, weight_decay=self.weight_decay)
    #
    #     best_acc_val = 0
    #
    #     for i in range(train_iters):
    #         if i == train_iters // 2:
    #             lr = self.lr * 0.1
    #             optimizer = optim.Adam(self.parameters(), lr=lr, weight_decay=self.weight_decay)
    #
    #         self.train()
    #         optimizer.zero_grad()
    #         output = self.forward(self.features, self.adj_norm)
    #         loss_train = self.loss(output, labels)
    #         loss_train.backward()
    #         optimizer.step()
    #
    #         if verbose and i % 100 == 0:
    #             print('Epoch {}, training loss: {}'.format(i, loss_train.item()))
    #
    #         with torch.no_grad():
    #             self.eval()
    #             output = self.forward(feat_full, adj_full_norm)
    #             if adj_val:
    #                 loss_val = F.nll_loss(output, labels_val)
    #                 acc_val = utils.accuracy(output, labels_val)
    #             else:
    #                 loss_val = F.nll_loss(output[data.idx_val], labels_val)
    #                 acc_val = utils.accuracy(output[data.idx_val], labels_val)
    #
    #             if acc_val > best_acc_val:
    #                 best_acc_val = acc_val
    #                 self.output = output
    #                 weights = deepcopy(self.state_dict())
    #
    #     if verbose:
    #         print('=== picking the best model according to the performance on validation ===')
    #     self.load_state_dict(weights)
    #
    # def test(self, idx_test):
    #     """Evaluate GCN performance on test set.
    #     Parameters
    #     ----------
    #     idx_test :
    #         node testing indices
    #     """
    #     self.eval()
    #     output = self.predict()
    #     # output = self.output
    #     loss_test = F.nll_loss(output[idx_test], self.labels[idx_test])
    #     acc_test = utils.accuracy(output[idx_test], self.labels[idx_test])
    #     print("Test set results:",
    #           "loss= {:.4f}".format(loss_test.item()),
    #           "accuracy= {:.4f}".format(acc_test.item()))
    #     return acc_test.item()
    #
    # @torch.no_grad()
    # def predict(self, features=None, adj=None):
    #     """By default, the inputs should be unnormalized adjacency
    #     Parameters
    #     ----------
    #     features :
    #         node features. If `features` and `adj` are not given, this function will use previous stored `features` and `adj` from training to make predictions.
    #     adj :
    #         adjcency matrix. If `features` and `adj` are not given, this function will use previous stored `features` and `adj` from training to make predictions.
    #     Returns
    #     -------
    #     torch.FloatTensor
    #         output (log probabilities) of GCN
    #     """
    #
    #     self.eval()
    #     if features is None and adj is None:
    #         return self.forward(self.features, self.adj_norm)
    #     else:
    #         if type(adj) is not torch.Tensor:
    #             features, adj = utils.to_tensor(features, adj, device=self.device)
    #
    #         self.features = features
    #         if utils.is_sparse_tensor(adj):
    #             self.adj_norm = utils.normalize_adj_tensor(adj, sparse=True)
    #         else:
    #             self.adj_norm = utils.normalize_adj_tensor(adj)
    #         return self.forward(self.features, self.adj_norm)
    #
    # @torch.no_grad()
    # def predict_unnorm(self, features=None, adj=None):
    #     self.eval()
    #     if features is None and adj is None:
    #         return self.forward(self.features, self.adj_norm)
    #     else:
    #         if type(adj) is not torch.Tensor:
    #             features, adj = utils.to_tensor(features, adj, device=self.device)
    #
    #         self.features = features
    #         self.adj_norm = adj
    #         return self.forward(self.features, self.adj_norm)
