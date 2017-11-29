# import path related modules 
import sys
import os
from os.path import dirname, realpath
sys.path.append(dirname(dirname(realpath(__file__)))) ##add project path to system path list
os.chdir(dirname(dirname(realpath(__file__)))) #u'/Users/yafeihan/Dropbox (MIT)/Courses_MIT/6.864_NLP/NLP_Final_Project'

import numpy as np
import operator
import sklearn as sk
import math, sys, nltk
import torch.nn as nn
import torch
import random
import time
from torch import autograd
from torch.autograd import Variable
import torch.optim as optim
import pdb
import torch.nn.functional as F
from optparse import OptionParser
from src.init_util import *
from src.data_util import *

def get_model(embeddings, ids_corpus, args):
    '''
    Build a model - a subclass of nn.Module, e.g. lstm, DAN, RNN etc. 
    given fixed embedding and a model parameters specified by user in args.
    
    args: embedding_dim=200, hidden_dim=300, batch_size=5, vocab_size=100407, embeddings=embeddings,hidden_layers=1
    '''
    print("\nBuilding model...")
    if args.model_name == 'lstm':
        return LSTM(embeddings,ids_corpus, args)
    else:
        raise Exception("Model name {} not supported!".format(args.model_name))    
    
class LSTM(nn.Module):  
    '''
    LSTM for learning similarity between questions 
    '''
    def __init__(self, embeddings,ids_corpus, args): 
        '''
        embeddings: fixed embedding table (2-D array, dim=vocab_size * embedding_dim: 100407x200)
        '''
        super(LSTM, self).__init__()  
        self.hidden_dim = args.hidden_dim
        self.hidden_layers = 1 #
        self.padding_idx = 0  
        self.batch_size = args.batch_size #number of source questions 
        self.vocab_size, self.embedding_dim = embeddings.shape
        self.args = args
        ##Define layers in the nnet
        self.embedding = nn.Embedding(self.vocab_size, self.embedding_dim, self.padding_idx)
        self.embedding.weight.data = torch.from_numpy(embeddings) #fixed embedding 
        self.lstm = nn.LSTM(
                            input_size = self.embedding_dim, 
                            hidden_size = self.hidden_dim, 
                            num_layers = self.hidden_layers, 
                            dropout=args.dropout)
        self.activation = get_activation_by_name('tanh')  ##can choose other activation function specified in args 
        #self.crit = nn.MultiMarginLoss(p=1, margin=0.2, weight=None, size_average=True)
        self.ids_corpus = ids_corpus
        
    def init_hidden(self,num_ques):
        '''
        Input:
            num_ques: number of unique questions (source query & candidate queries) in the current batch. 
            (NOTE:  diff from batch_size := num of source questions)             
        Return (h_0, c_0): hidden and cell state at position t=0
            h_0 (num_layers * num_directions=1, batch, hidden_dim): tensor containing the initial hidden state for each element in the batch.
            c_0 (num_layers * num_directions=1, batch, hidden_dim): tensor containing the initial cell state for each element in the batch.
        '''
        return (autograd.Variable(torch.zeros(self.hidden_layers, num_ques, self.hidden_dim),requires_grad = True),
                autograd.Variable(torch.zeros(self.hidden_layers, num_ques, self.hidden_dim),requires_grad = True))

    def forward(self, batch, if_training):
        '''
        Pass one batch
        Input:
            batch: one batch is a tuple (titles:2-d array, bodies:2-d array, triples:2-d array)
                titles: padded title word idx list for all titles in the batch  (seq_len_title * num_ques)
                bodies: padded body word idx list for all bodies in the batch  ((seq_len_body * num_ques))
                triples: each row is a source query and its candidate queries (1 pos,20 neg )
                        batch_size * 21
        Output:
            self.h_t: Variable, seq_len_title * num_ques * hidden_dim 
            self.h_b: Variable, seq_len_body * num_ques * hidden_dim
            self.h_final: Tensor, num_ques * hidden_dim
        '''
        titles,bodies,triples = batch 
        seq_len_t, num_ques= titles.shape
        seq_len_b, num_ques= bodies.shape
        self.hcn_t = self.init_hidden(num_ques) #(h_0, c_0) for titles' initial hidden states
        self.hcn_b = self.init_hidden(num_ques) #(h_0, c_0) for bodies' initial hidden states
        
        ## embedding layer: word indices => embeddings 
        embeds_titles = self.embedding(Variable(torch.from_numpy(titles).long(),requires_grad=False)) #seq_len_title * num_ques * embed_dim
        embeds_bodies = self.embedding(Variable(torch.from_numpy(bodies).long(),requires_grad=False)) #seq_len_body * num_ques * embed_dim
        
        ## lstm layer: word embedding (200) & h_(t-1) (hidden_dim) => h_t (hidden_dim)
        h_t, self.hcn_t = self.lstm(embeds_titles, self.hcn_t)
        h_b, self.hcn_b = self.lstm(embeds_bodies, self.hcn_b)
        
        ## activation function 
        h_t = self.activation(h_t) #seq_len * num_ques * hidden_dim
        h_b = self.activation(h_b) #seq_len * num_ques * hidden_dim
        
        #if args.normalize:
        h_t = normalize_3d(h_t)
        h_b = normalize_3d(h_b)
        
        self.h_t = h_t #self.h_t: seq_len * num_ques * hidden_dim
        self.h_b = h_b #self.h_b: seq_len * num_ques * hidden_dim
        
        #if args.average:
        # Average over sequence length, ignoring paddings
        h_t_final = self.average_without_padding(h_t, titles) #h_t: num_ques * hidden_dim
        h_b_final = self.average_without_padding(h_b, bodies) #h_b: num_ques * hidden_dim
            #say("h_avg_title dtype: {}\n".format(ht.dtype))
#        else:
#            h_t_final = h_t[-1]  ## get the hidden output at the last position 
#            h_b_final = h_b[-1]  
        #Pool title and body hidden tensor together 
        h_final = (h_t_final+h_b_final)*0.5 # num_ques * hidden_dim
        #h_final = apply_dropout(h_final, dropout) ???
        h_final = normalize_2d(h_final) ##normalize along hidden_dim, hidden representation of a question has norm = 1
        self.h_final = h_final  #Tensor, num_ques * hidden_dim 
        return h_final
        
    def average_without_padding(self, x, ids,eps=1e-8):
        '''
        average hidden output over seq length ignoaring padding 
        
        Input: 
            x: Variable that contains hidden layer output tensor; size = seq_len * num_ques * hidden_dim
        Output: 
            avg: num_ques * hidden_dim
        '''
        mask = (ids<>self.padding_idx)*1.0
        seq_len, num_ques = mask.shape
        mask_tensor = torch.from_numpy(mask).float().view((seq_len, num_ques,-1)) #mask_tensor: seq_len * batch * 1
        mask_tensor = mask_tensor.expand((seq_len, num_ques,self.hidden_dim)) #repeat the last dim to match hidden layer dimension
        mask_variable = Variable(mask_tensor,requires_grad = True)
        avg = (x*mask_variable).sum(dim=0)/(mask_variable.sum(dim=0)+eps)
        return avg
    
    def evaluate(self, data):
        '''
        Input: 
            data: dev or test data 
                 a list of tuples: (pid, [qid,...],[label,..])  output from read_annotations()
                 20 qids and 20 labels corresponding to the candidate set of source query p. 
        Output: 
            Pass through neural net and compute accuracy measures 
        '''
        res = []
        eval_batches = create_eval_batches(self.ids_corpus, data, padding_id=0, pad_left=False) 
        #each srouce query corresponds to one batch: (pid, [qid,...],[label,..]) 
        for batch in eval_batches:
            titles,bodies,labels = batch
            h_final = self.forward(batch,False) #not training 
            scores = cosSim(h_final)
            assert len(scores) == len(labels) #20
            ranks = np.array((-scores.data).tolist()).argsort() #sort by score 
            ranked_labels = labels[ranks]
            res.append(ranked_labels) ##a list of labels for the ranked retrievals  
        e = Evaluation(res)
        MAP = e.MAP()*100
        MRR = e.MRR()*100
        P1 = e.Precision_at_R(1)*100
        P5 = e.Precision_at_R(5)*100
        return MAP, MRR, P1, P5
 
    def get_pnorm_stat(self):
        '''
        get params norms
        '''
        lst_norms = []
        for p in self.parameters():
            lst_norms.append("{:.3f}".format(p.norm(2).data[0]))
        return lst_norms
    
    def get_l2_reg(self):
        l2_reg = None
        for p in self.parameters():
            if l2_reg is None:
                l2_reg = p.norm(2)
            else:
                l2_reg = l2_reg + p.norm(2)
        l2_reg = l2_reg * self.args.l2_reg
        return l2_reg
    

def max_margin_loss(h_final,batch,margin):
    '''
    Post process h_final: Compute average max margin loss for a batch 
    '''
    titles,bodies,triples = batch
    hidden_dim = h_final.size(1)
    
    queSet_vectors = h_final[torch.from_numpy(triples.ravel()).long()] #flatten triples question indices to a 1-D Tensor of len = source queries *22
    queSet_vectors = queSet_vectors.view(triples.shape[0],triples.shape[1],hidden_dim) #num of query * 22 * hidden_dim
    
    # num of query * hidden_dim
    src_vecs = queSet_vectors[:,0,:] #source query * hidden_dim (source query)
    pos_vecs = queSet_vectors[:,1,:]  #source query * hidden_dim  (1 pos sample)
    neg_vecs = queSet_vectors[:,2:,:] #source query * 20 * hidden_dim   (20 negative samples )
    
    pos_scores = (src_vecs * pos_vecs).sum(dim = 1) # 1-d Tensor: num queries 
    
    #add 1 dimemnsion, and repeat to match neg_vecs shape 
    src_vecs_repeat = src_vecs.double().view(src_vecs.size()[0],-1,src_vecs.size()[1]).expand(neg_vecs.size()).float()
    neg_scores = (src_vecs_repeat * neg_vecs).sum(dim = 2) #cosine similarity: sum product over hidden dimension. source query * 20
    neg_scores_max,index_max = neg_scores.max(dim=1) # max over all 20 negative samples  # 1-d Tensor: num source queries 
    diff = neg_scores_max - pos_scores + margin   #1-d tensor  length = num of source queries    
    loss = ((diff>0).float()*diff).mean() #average loss over all source queries in a batch 
    return loss

### use avg margin loss       
#        x_scores = torch.cat((pos_scores.view(pos_scores.size()[0],-1),neg_scores),dim=1)
#        y_scores = Variable(torch.LongTensor(x_scores.size()[0]).zero_())
#        loss=self.crit(x_scores,y_scores)
#        self.loss = loss

def cosSim(h_final):
    '''
        Post process h_final for dev or test: Compute the cosine similarities
        first row in batch is source query, the rest are candidate questions
    '''
    hidden_dim = h_final.size(1)
    source = h_final[0] #first row in h_final is the source query's hidden layer output  
    candidates = h_final[1:] #2nd row beyond in h_final are the candidate query's hidden layer output  
    source = source.view(-1,hidden_dim).expand(candidates.size()) 
    cosSim = (source * candidates).sum(dim=1) 
    return cosSim

def normalize_3d(x,eps=1e-8):
        '''
        Normalize a Variable containing a 3d tensor on axis 2
        Input: Variable 
            x: seq_len * num_ques * hidden_dim
        Output: Variable 
            a normalized x (normalized on the last dim)
        '''
        l2 = x.norm(p=2,dim=2).view(x.data.shape[0],x.data.shape[1],1) 
        #make sure l2 dim = x dim = seq_len * num_ques * hidden_dim
        return x/(l2+eps)

def normalize_2d(x, eps=1e-8):
    # x is batch*hidden_dim
    # l2 is batch*1
    l2 = x.norm(2,dim=1)  #l2: 1d tensor of dim = num_ques
    l2 = l2.view(len(l2),-1) #change l2's dimension to: num_ques * 1
    return x/(l2+eps)  

class Evaluation():
    def __init__(self,data):
        self.data = data
    
    def Precision_at_R(self,precision_at):
        precision_all = []
        for item in self.data:
            item_sel = item[:precision_at]
            count_pos = 0.0
            if len(item_sel)>0:
                for label in item_sel:
                    if label == 1:
                        count_pos += 1
                precision_all.append(count_pos/len(item_sel))
            else:
                precision_all.append(0.0)
        return sum(precision_all)/len(precision_all)
                
    def MAP(self):
        '''
        Mean Average Precision (MAP)
        
        Input: 
            self.data: a list of ranked retrievals' labels(1=relevant,0=not rel)
        Output: 
            float MAP
        '''
        AP = [] #list of Average Precision(AP) for all queries in self.data
        for item in self.data: #examine each query
            count_pos = 0.0 ##accumulative count of relevant documents 
            Pk = [] #precision of the first (k+1) retrievals for a single query
            for k,label in enumerate(item): #k: rank of retrieved doc, label: 1 if relevant, 0 not relevant
                if label == 1:
                    count_pos += 1.0
                Pk.append(count_pos/(k+1)) #precision for the first (k+1) retrievals
            if len(Pk)>0: 
                AP.append(sum(Pk)/len(Pk))
            else:
                AP.append(0.0)
        return sum(AP)/len(AP) #average over all queries
    
    def MRR(self):
        '''
        Mean reciprocal rank (MRR)
        MRR = 1/|Q| * sum_j(1/rank_j), where 
            Q: set of all queries,|Q| is the number of queries 
            rank_j: the rank of the first relevant document for query j in Q
            
        '''
        list_RR = [] #list of reciprocal rank for all queries
        for item in self.data:
            if len(item)==0: #no retrieval for current query
                list_RR.append(0.0)
            else:
                for i,label in enumerate(item):
                    if label == 1: #first encountering a relevant document 
                        list_RR.append(1.0/(i+1))  #record 1/rank 
                        break
                    if i==len(item)-1:#reach the end but not find relevant document
                        list_RR.append(0.0)  
        return sum(list_RR)/len(list_RR) if len(list_RR) > 0 else 0.0






 
    