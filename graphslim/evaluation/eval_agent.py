import numpy as np
import scipy.sparse as sp
import torch
import torch.nn as nn
import torch.nn.functional as F

from graphslim import utils
from graphslim.models import GCN1, GAT, APPNP1


class Evaluator:

    def __init__(self, data, args, device='cuda', **kwargs):
        self.data = data
        self.args = args
        self.device = device
        # self.args.runs = 10
        n = int(data.feat_train.shape[0] * args.reduction_rate)
        d = data.feat_train.shape[1]
        self.nnodes_syn = n
        self.adj_param= nn.Parameter(torch.FloatTensor(n, n).to(device))
        self.feat_syn = nn.Parameter(torch.FloatTensor(n, d).to(device))
        self.labels_syn = torch.LongTensor(self.generate_labels_syn(data)).to(device)
        self.reset_parameters()
        # print('adj_param:', self.adj_param.shape, 'feat_syn:', self.feat_syn.shape)

    def reset_parameters(self):
        self.adj_param.data.copy_(torch.randn(self.adj_param.size()))
        self.feat_syn.data.copy_(torch.randn(self.feat_syn.size()))

    def generate_labels_syn(self, data):
        from collections import Counter
        counter = Counter(data.labels_train.tolist())
        num_class_dict = {}
        n = len(data.labels_train)

        sorted_counter = sorted(counter.items(), key=lambda x:x[1])
        sum_ = 0
        labels_syn = []
        self.syn_class_indices = {}
        for ix, (c, num) in enumerate(sorted_counter):
            if ix == len(sorted_counter) - 1:
                num_class_dict[c] = int(n * self.args.reduction_rate) - sum_
                print(num_class_dict[c])
                self.syn_class_indices[c] = [len(labels_syn), len(labels_syn) + num_class_dict[c]]
                labels_syn += [c] * num_class_dict[c]
            else:
                num_class_dict[c] = max(int(num * self.args.reduction_rate), 1)
                sum_ += num_class_dict[c]
                self.syn_class_indices[c] = [len(labels_syn), len(labels_syn) + num_class_dict[c]]
                labels_syn += [c] * num_class_dict[c]

        self.num_class_dict = num_class_dict
        return labels_syn


    def test_gat(self, nlayers, model_type, verbose=False):
        res = []
        args = self.args

        if args.dataset in ['cora', 'citeseer']:
            args.epsilon = 0.5 # Make the graph sparser as GAT does not work well on dense graph
        else:
            args.epsilon = 0.01

        print('======= testing %s' % model_type)
        data, device = self.data, self.device


        feat_syn, adj_syn, labels_syn = self.get_syn_data(model_type)
        # with_bn = True if self.args.dataset in ['ogbn-arxiv'] else False
        with_bn = False
        if model_type == 'GAT':
            model = GAT(nfeat=feat_syn.shape[1], nhid=16, heads=16, dropout=0.0,
                        weight_decay=0e-4, nlayers=self.args.nlayers, lr=0.001,
                        nclass=data.nclass, device=device, dataset=self.args.dataset).to(device)


        noval = True if args.dataset in ['reddit', 'flickr'] else False
        model.fit(feat_syn, adj_syn, labels_syn, np.arange(len(feat_syn)), noval=noval, data=data,
                     train_iters=10000 if noval else 3000, normalize=True, verbose=verbose)

        model.eval()
        labels_test = torch.LongTensor(data.labels_test).cuda()

        if args.dataset in ['reddit', 'flickr']:
            output = model.predict(data.feat_test, data.adj_test)
            loss_test = F.nll_loss(output, labels_test)
            acc_test = utils.accuracy(output, labels_test)
            res.append(acc_test.item())
            if verbose:
                print("Test set results:",
                      "loss= {:.4f}".format(loss_test.item()),
                      "accuracy= {:.4f}".format(acc_test.item()))

        else:
            # Full graph
            output = model.predict(data.feat_full, data.adj_full)
            loss_test = F.nll_loss(output[data.idx_test], labels_test)
            acc_test = utils.accuracy(output[data.idx_test], labels_test)
            res.append(acc_test.item())
            if verbose:
                print("Test set results:",
                      "loss= {:.4f}".format(loss_test.item()),
                      "accuracy= {:.4f}".format(acc_test.item()))

        labels_train = torch.LongTensor(data.labels_train).cuda()
        output = model.predict(data.feat_train, data.adj_train)
        loss_train = F.nll_loss(output, labels_train)
        acc_train = utils.accuracy(output, labels_train)
        if verbose:
            print("Train set results:",
                  "loss= {:.4f}".format(loss_train.item()),
                  "accuracy= {:.4f}".format(acc_train.item()))
        res.append(acc_train.item())
        return res

    def get_syn_data(self, model_type=None, verbose=False):
        data, device = self.data, self.device
        feat_syn, adj_param, labels_syn = self.feat_syn.detach(), \
            self.adj_param.detach(), self.labels_syn

        args = self.args
        # import os
        # print(os.path.abspath(__file__))
        adj_syn = torch.load(f'../dataset/output/saved_ours/adj_{args.dataset}_{args.reduction_rate}_{args.seed}.pt', map_location='cuda')
        feat_syn = torch.load(f'../dataset/output/saved_ours/feat_{args.dataset}_{args.reduction_rate}_{args.seed}.pt', map_location='cuda')

        if model_type == 'MLP':
            adj_syn = adj_syn.to(self.device)
            adj_syn = adj_syn - adj_syn
        else:
            adj_syn = adj_syn.to(self.device)

        if verbose:
            print('Sum:', adj_syn.sum(), adj_syn.sum()/(adj_syn.shape[0]**2))
            print('Sparsity:', adj_syn.nonzero().shape[0]/(adj_syn.shape[0]**2))

        if self.args.epsilon > 0:
            adj_syn[adj_syn < self.args.epsilon] = 0
            if verbose:
                print('Sparsity after truncating:', adj_syn.nonzero().shape[0]/(adj_syn.shape[0]**2))
        feat_syn = feat_syn.to(self.device)

        # edge_index = adj_syn.nonzero().T
        # adj_syn = torch.sparse.FloatTensor(edge_index,  adj_syn[edge_index[0], edge_index[1]], adj_syn.size())


        return feat_syn, adj_syn, labels_syn


    def test(self, model_type, verbose=True):
        res = []

        args = self.args
        data, device = self.data, self.device

        feat_syn, adj_syn, labels_syn = self.get_syn_data(model_type)

        if verbose:
            print('======= testing %s' % model_type)
        if model_type == 'MLP':
            model_class = GCN1
        else:
            model_class = eval(model_type)
        weight_decay = 5e-4
        dropout = 0.5 if args.dataset in ['reddit'] else 0

        if verbose:
            print(type(data.nclass))
        model = model_class(nfeat=feat_syn.shape[1], nhid=args.hidden, dropout=dropout,
                    weight_decay=weight_decay, nlayers=args.nlayers,
                    nclass=data.nclass, device=device, activation=args.activation).to(device)

        # with_bn = True if self.args.dataset in ['ogbn-arxiv'] else False
        if args.dataset in ['ogbn-arxiv', 'arxiv']:
            model = model_class(nfeat=feat_syn.shape[1], nhid=args.hidden, dropout=0.,
                        weight_decay=weight_decay, nlayers=nlayers, with_bn=False,
                        nclass=data.nclass, device=device).to(device)

        # val = False if args.dataset in ['reddit', 'flickr'] else True
        val = False
        model.fit_with_val(feat_syn, adj_syn, labels_syn, data,
                     train_iters=600, normalize=True, verbose=verbose, val=val)

        model.eval()
        labels_test = torch.LongTensor(data.labels_test).cuda()

        if model_type == 'MLP':
            output = model.predict_unnorm(data.feat_test, sp.eye(len(data.feat_test)))
        else:
            output = model.predict(data.feat_test, data.adj_test)

        if args.dataset in ['reddit', 'flickr']:
            loss_test = F.nll_loss(output, labels_test)
            acc_test = utils.accuracy(output, labels_test)
            res.append(acc_test.item())
            if verbose:
                print("Test set results:",
                      "loss= {:.4f}".format(loss_test.item()),
                      "accuracy= {:.4f}".format(acc_test.item()))

        # if not args.dataset in ['reddit', 'flickr']:
        else:
            # Full graph
            output = model.predict(data.feat_full, data.adj_full)
            loss_test = F.nll_loss(output[data.idx_test], labels_test)
            acc_test = utils.accuracy(output[data.idx_test], labels_test)
            res.append(acc_test.item())
            if verbose:
                print("Test full set results:",
                      "loss= {:.4f}".format(loss_test.item()),
                      "accuracy= {:.4f}".format(acc_test.item()))

            labels_train = torch.LongTensor(data.labels_train).cuda()
            output = model.predict(data.feat_train, data.adj_train)
            loss_train = F.nll_loss(output, labels_train)
            acc_train = utils.accuracy(output, labels_train)
            if verbose:
                print("Train set results:",
                      "loss= {:.4f}".format(loss_train.item()),
                      "accuracy= {:.4f}".format(acc_train.item()))
            res.append(acc_train.item())
        return res

    def train_cross(self, verbose=True):
        args = self.args
        data = self.data
        data.nclass = data.nclass.item()

        final_res = {}
        runs = self.args.nruns

        for model_type in ['GCN1', 'GraphSage', 'SGC1', 'MLP', 'APPNP1', 'Cheby']:
            res = []
            for i in range(runs):
                res.append(self.test(model_type=model_type, verbose=False))
            res = np.array(res)
            print('Test/Train Mean Accuracy:',
                  repr([res.mean(0), res.std(0)]))
            final_res[model_type] = [res.mean(0), res.std(0)]


        # print('=== testing GAT')
        # res = []
        # nlayer = 2
        # for i in range(runs):
        #     res.append(self.test_gat(verbose=True, nlayers=nlayer, model_type='GAT'))
        # res = np.array(res)
        # print('Layer:', nlayer)
        # print('Test/Full Test/Train Mean Accuracy:',
        #         repr([res.mean(0), res.std(0)]))
        # final_res['GAT'] = [res.mean(0), res.std(0)]

        print('Final result:', final_res)

    def train(self, model_type, verbose=True):
        # model_type: ['GCN1', 'GraphSage', 'SGC1', 'MLP', 'APPNP1', 'Cheby']
        args = self.args
        data = self.data
        data.nclass = data.nclass

        final_res = {}
        runs = self.args.runs
        res = []
        for i in range(runs):
            res.append(self.test(model_type=model_type, verbose=False))
            break
        res = np.array(res)

        if runs > 1:
            return res
        else:
            return res[0][0]
        # print('Test/Train Mean Accuracy:',
        #       repr([res.mean(0), res.std(0)]))
        # final_res[model_type] = [res.mean(0), res.std(0)]
        #
        # print('Final result:', final_res)


