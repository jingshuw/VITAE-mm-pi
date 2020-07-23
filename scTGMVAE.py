import numpy as np
import scipy as sp
import pandas as pd
import networkx as nx
from sklearn.metrics.cluster import adjusted_rand_score
import warnings
import os

import model
import preprocess
import train
from inference import Inferer
from utils import get_igraph, louvain_igraph, plot_clusters
from metric import topology, compute_F1_score

class scTGMVAE():
    """
    class for Gaussian Mixture Model for trajectory analysis
    """
    def __init__(self):
        pass

    # get data for model
    # X: 2-dimension np array, original counts data
    # labels: a list of labelss for cells
    # cell_names: a list of cell names
    # gene_names: a list of gene names
    def get_data(self, X, labels = None, cell_names = None, gene_names = None):
        self.X = X.astype(np.float32)
        if sp.sparse.issparse(self.X):
            self.X = self.X.toarray()
        self.label_names = None if labels is None else np.array(labels, dtype = str)
        self.raw_cell_names = None if cell_names is None else np.array(cell_names, dtype = str)
        self.raw_gene_names = None if gene_names is None else np.array(gene_names, dtype = str)


    # data preprocessing, feature selection, log-normalization
    # K: the constant summing gene expression in each cell up to
    # gene_num: number of feature to select
    def preprocess_data(self, K = 1e4, gene_num = 2000):
        self.X_normalized, self.X, self.cell_names, self.gene_names, \
        self.scale_factor, self.labels, self.label_names, \
        self.le, self.gene_scalar = preprocess.preprocess(
            self.X.copy(),
            self.label_names,
            self.raw_cell_names,
            self.raw_gene_names,
            K,
            gene_num)
        self.dim_origin = self.X.shape[1]


    # get parameters, wrap up training dataset and initialize the Variational Auto Encoder model
    # n_clusters: number of Gaussian Mixtures, number of cell types
    # dimensions: a list of dimensions of layers of autoencoder between latent space and original space
    # dim_latent: dimension of latent space
    # data_type: 'UMI' and 'non-UMI', default is 'UMI'
    # NUM_EPOCH_PRE: number of epochs for pre training
    # NUM_EPOCH: number of epochs for training
    # NUM_STEP_PER_EPOCH: number of steps in each epoch, default is n/BATCH_SIZE+1
    def build_model(self,
        dimensions = [16], 
        dim_latent = 8,
        L = 5,
        data_type = 'UMI',
        save_weights = False,
        path_to_weights_pretrain = 'pre_train.checkpoint',
        path_to_weights_train = 'train.checkpoint'
        ):
        self.dimensions = dimensions
        self.dim_latent = dim_latent
        self.L = L
        self.data_type = data_type
        self.save_weights = save_weights
        self.path_to_weights_pretrain = path_to_weights_pretrain
        self.path_to_weights_train = path_to_weights_train
    
        self.vae = model.VariationalAutoEncoder(
            self.dim_origin, 
            self.dimensions, 
            self.dim_latent,
            self.L,
            self.data_type
            )
        
        
    # save and load trained model parameters
    # path: path of checkpoints files
    def save_model(self, path_to_file='model.checkpoint'):
        self.vae.save_weights(path_to_file)
    
    
    def load_model(self, path_to_file='model.checkpoint', n_clusters=None):
        '''
        Params:
            path_to_file - path to weight files of pre trained or
                           trained model
            n_clusters   - if n_cluster is provided, then the GMM layer
                           will be initialized or re-initialized. For loading
                           a trained model when the GMM layer is not
                           initialized, n_cluster is required.                           
        '''
        if n_clusters is not None:
            self.init_GMM(n_clusters)
        self.vae.load_weights(path_to_file)        


    # pre train the model with specified learning rate
    def pre_train(self, learning_rate = 1e-3, batch_size = 32,
            num_epoch = 300, num_step_per_epoch = None,
            early_stopping_patience = 10, early_stopping_tolerance = 1e-3, L=None):
            
        if num_step_per_epoch is None:
            num_step_per_epoch = self.X.shape[0]//batch_size+1
                
        train.clear_session()
        self.train_dataset = train.warp_dataset(self.X_normalized, batch_size, self.X, self.scale_factor)
        self.vae = train.pre_train(
            self.train_dataset, 
            self.vae, 
            learning_rate, 
            early_stopping_patience,
            early_stopping_tolerance,
            num_epoch,
            num_step_per_epoch,
            L)
        if self.save_weights:
            self.save_model(self.path_to_weights_pretrain)
          

    def get_latent_z(self):
        return self.vae.get_z(self.X_normalized)


    def init_GMM(self, n_clusters, cluster_labels=None, mu=None, pi=None):
        self.n_clusters = n_clusters
        self.cluster_labels = None if cluster_labels is None else np.array(cluster_labels)
        self.vae.init_GMM(n_clusters, mu, pi)
        self.inferer = Inferer(self.n_clusters)


    # train the model with specified learning rate
    def train(self, learning_rate = 1e-3, batch_size = 32,
            num_epoch = 300, num_step_per_epoch = None,
            early_stopping_patience = 10, early_stopping_tolerance = 1e-3,
            L=None, weight=None, plot_every_num_epoch=None):
        
        if num_step_per_epoch is None:
            num_step_per_epoch = self.X.shape[0]//batch_size+1
            
        self.train_dataset = train.warp_dataset(self.X_normalized, batch_size, self.X, self.scale_factor)
        self.test_dataset = train.warp_dataset(self.X_normalized, batch_size)
        self.vae = train.train(
            self.train_dataset,
            self.test_dataset,
            self.vae, 
            learning_rate,
            early_stopping_patience,
            early_stopping_tolerance,
            num_epoch,
            num_step_per_epoch,
            L,
            self.labels,
            weight,
            plot_every_num_epoch
            )
        if self.save_weights:
            self.save_model(self.path_to_weights_train)
          
          
    # train the model with specified learning rate
    def train_all(self, learning_rate = 1e-3, batch_size = 32,
            num_epoch = 300, num_step_per_epoch = None,
            early_stopping_patience = 10, early_stopping_tolerance = 1e-3,
            L=None, weight=None, plot_every_num_epoch=None):
        '''
        To pretrain and train the model by using same parameters for pre_train() and train().
        '''
        train.clear_session()
        self.pre_train(learning_rate,
            batch_size,
            num_epoch,
            num_step_per_epoch,
            early_stopping_patience,
            early_stopping_tolerance,
            L)
        self.init_GMM_plot()
        self.train(learning_rate,
            batch_size,
            num_epoch,
            num_step_per_epoch,
            early_stopping_patience,
            early_stopping_tolerance,
            L,
            weight,
            is_plot)
        return None
        

    # inference for trajectory
    def init_inference(self, batch_size=32, L=5):
        self.test_dataset = train.warp_dataset(self.X_normalized, batch_size)
        _, self.mu,self.c,self.pc_x,self.w,self.var_w,self.wc,self.var_wc,self.w_tilde,self.var_w_tilde,self.z = self.vae.inference(self.test_dataset, L=L)
        self.inferer.init_embedding(self.z, self.mu)
        return None
        
        
    def comp_inference_score(self, thres=0.5, method='mean', no_loop=False, path=''):
        G, edges = self.inferer.init_inference(self.w_tilde, self.pc_x, thres, method, no_loop)
        self.inferer.plot_clusters(self.cluster_labels, path=path)
        return G
        
        
    def plot_trajectory(self, init_node: int, cutoff=None, path=''):
        w, pseudotime = self.inferer.plot_trajectory(init_node, self.label_names, cutoff, path=path)
        return w, pseudotime

    
    def plot_marker_gene(self, gene_name: str):
        if gene_name not in self.gene_names:
            raise ValueError("Gene name '{}' not in selected genes!".format(gene_name))
        expression = self.X_normalized[:,self.gene_names==gene_name].flatten()
        self.inferer.plot_marker_gene(expression, gene_name)
        return None


    def evaluate(self, milestone_net, method='mean', path=''):
        begin_node = int(np.argmin(np.mean((
            self.z[(milestone_net['from']==0)&(milestone_net['to']==0),:,np.newaxis] -
            self.mu[np.newaxis,:,:])**2, axis=(0,1))))
        
        G, edges = self.inferer.init_inference(self.w_tilde, self.pc_x, 0.5, method, True)
        w, pseudotime = self.inferer.plot_trajectory(begin_node, self.label_names, cutoff=None, path=path, is_plot=False)
        
        # 1. Topology
        G_pred = nx.Graph()
        G_pred.add_edges_from(G.edges)
        nx.set_node_attributes(G_pred, False, 'is_init')
        G_pred.nodes[begin_node]['is_init'] = True

        G_true = nx.Graph()
        G_true.add_edges_from(list(
            milestone_net[~pd.isna(milestone_net['w'])].groupby(['from', 'to']).count().index))
        nx.set_node_attributes(G_true, False, 'is_init')
        G_true.nodes[0]['is_init'] = True
        res = topology(G_true, G_pred)
            
        # 2. Milestones assignment
        milestones_pred = np.argmax(w[pseudotime!=-1,:], axis=1)
        milestones_true = milestone_net['from'].values.copy()
        milestones_true[(milestone_net['from']!=milestone_net['to'])
                       &(milestone_net['w']<0.5)] = milestone_net[(milestone_net['from']!=milestone_net['to'])
                                                                  &(milestone_net['w']<0.5)]['to'].values
        milestones_true = milestones_true[pseudotime!=-1]        
        res['score_ARI'] = (adjusted_rand_score(milestones_true, milestones_pred) + 1)/2
        
        # 3. Correlation between geodesic distances / Pseudotime
        pseudotime_ture = milestone_net['from'].values + 1 - milestone_net['w'].values
        pseudotime_ture[np.isnan(pseudotime_ture)] = milestone_net[pd.isna(milestone_net['w'])]['from'].values
        pseudotime_ture = pseudotime_ture[pseudotime>-1]
        pseudotime_pred = pseudotime[pseudotime>-1]
        res['score_cor'] = np.corrcoef(pseudotime_ture,pseudotime_pred)[0,1]
        
        # 4. Shape
        score_cos_theta = 0
        for (_from,_to) in G.edges:
            _z = self.z[(w[:,_from]>0) & (w[:,_to]>0),:]
            v_1 = _z - self.mu[:,_from]
            v_2 = _z - self.mu[:,_to]
            cos_theta = np.sum(v_1*v_2, -1)/(np.linalg.norm(v_1,axis=-1)*np.linalg.norm(v_2,axis=-1)+1e-12)

            score_cos_theta += np.sum((1-cos_theta)/2)

        res['score_cos_theta'] = score_cos_theta/np.sum(np.sum(w>0, axis=-1)==2)
        return res
