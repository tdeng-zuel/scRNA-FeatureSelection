import numpy as np
import anndata as ad
import warnings
import time
import datetime
import pickle
from typing import List, Union
from utils.utils import head, delete
from config import assign_cfg, cluster_cfg, exp_cfg
import pandas as pd


class PerformanceRecorder:
    """
    Record MRR, number of marker gene found, computation time, metrics of downstream analysis.
    Single method on single dataset with single dataset type.
    (F1: 5 folds; ARI: 2 splits, 5 folds respectively)
    """

    def __init__(self, task: str, data_name: str, methods: List[str], adata: ad.AnnData, n_gene: int = 1000):

        self.data_name = data_name
        self.task = task
        self.methods = methods
        self.adata = adata
        self.cell_type_counts = adata.obs['celltype'].value_counts(ascending=True)
        self.n_marker_contain = adata.uns['markers'].shape[0]
        self.n_genes = n_gene
        self.former_methods = None

        if task == 'assign':
            self.save_name = exp_cfg.record_path + '-'.join([self.data_name, self.task, str(self.n_genes)]) + '.pkl'

            self.MRR = pd.DataFrame(np.zeros((assign_cfg.n_folds, len(methods))), columns=methods)
            self.markers_found = pd.DataFrame(np.zeros((assign_cfg.n_folds, len(methods))), columns=methods)

            self.singlecellnet_F1 = pd.DataFrame(np.zeros((assign_cfg.n_folds, len(methods))), columns=methods)
            self.singleR_F1 = pd.DataFrame(np.zeros((assign_cfg.n_folds, len(methods))), columns=methods)
            self.itclust_F1 = pd.DataFrame(np.zeros((assign_cfg.n_folds, len(methods))), columns=methods)

            if self.cell_type_counts[0] >= assign_cfg.n_folds * 2:  # to ensure that each fold contains rare cells
                self.rare_type = self.cell_type_counts.index[0]

                self.singlecellnet_F1_rare = pd.DataFrame(np.zeros((assign_cfg.n_folds, len(methods))), columns=methods)
                self.singleR_F1_rare = pd.DataFrame(np.zeros((assign_cfg.n_folds, len(methods))), columns=methods)
                self.itclust_F1_rare = pd.DataFrame(np.zeros((assign_cfg.n_folds, len(methods))), columns=methods)

            self.computation_time = pd.DataFrame(np.zeros((assign_cfg.n_folds, len(methods))), columns=methods)
            self.init_evaluated_methods()

        elif task == 'cluster':
            self.save_name = exp_cfg.record_path + '-'.join([self.data_name, self.task, str(self.n_genes)]) + '.pkl'

            self.MRR = pd.DataFrame(np.zeros((cluster_cfg.n_loops, len(methods))), columns=methods)
            self.markers_found = pd.DataFrame(np.zeros((cluster_cfg.n_loops, len(methods))), columns=methods)

            self.seurat_ARI = pd.DataFrame(np.zeros((cluster_cfg.n_loops, len(methods))), columns=methods)
            self.sc3_ARI = pd.DataFrame(np.zeros((cluster_cfg.n_loops, len(methods))), columns=methods)
            self.cidr_ARI = pd.DataFrame(np.zeros((cluster_cfg.n_loops, len(methods))), columns=methods)

            if self.cell_type_counts[0] >= cluster_cfg.n_folds * 2:
                self.rare_type = self.cell_type_counts.index[0]

                self.seurat_F1_rare = pd.DataFrame(np.zeros((cluster_cfg.n_loops, len(methods))), columns=methods)
                self.sc3_F1_rare = pd.DataFrame(np.zeros((cluster_cfg.n_loops, len(methods))), columns=methods)
                self.cidr_F1_rare = pd.DataFrame(np.zeros((cluster_cfg.n_loops, len(methods))), columns=methods)

            self.computation_time = pd.DataFrame(np.zeros((cluster_cfg.n_loops, len(methods))), columns=methods)
            self.init_evaluated_methods()

        else:
            raise ValueError(f"{task} is an invalid argument.")

        self.current_fold = None
        self.current_method = None
        self.save_cmpt_time = None

        self.start_time = 0

    def init_evaluated_methods(self):
        try:
            with open(self.save_name, 'rb') as f:
                former_record = pickle.load(f)
            for prop in [item for item in dir(former_record) if not item.startswith('_')]:
                former_df = getattr(former_record, prop)
                if isinstance(former_df, pd.DataFrame):
                    if self.former_methods is None:
                        self.former_methods = former_df.columns.tolist()
                    else:
                        if former_df.columns.tolist() != self.former_methods:
                            raise RuntimeError(f"{prop} with methods {former_df.columns.tolist()}"
                                               f" are not equal to {self.former_methods}")
                    getattr(self, prop).loc[:, np.intersect1d(self.methods, former_df.columns.tolist())] = former_df
        except FileNotFoundError:
            pass

    def init_current_feature_selection_method(self, method: str, fold: int):
        delete('tempData/')
        head(name=method, fold=fold + 1)
        if self.former_methods is not None and method in self.former_methods:  # there is a former record
            print(f"This method has been evaluated on {self.data_name.capitalize()} dataset and will be ignored.")
            pass
        else:
            self.current_method = method
            self.current_fold = fold
            self.save_cmpt_time = True
            if '+' in method:
                self.base_methods = method.split('+')
                self.count_base_method = 0

    def show_dataset_info(self):
        head(name='Dataset Information')
        print("Name:{:^15}  Cell(s):{:<5d}  Gene(s):{:<5d}  Marker Gene(s):{:<4d}\nCell Types:{}".format(
            self.data_name, self.adata.n_obs, self.adata.n_vars, self.n_marker_contain,
            self.adata.obs['celltype'].unique()
        ))
        if hasattr(self, 'rare_type'):
            print("Rare Cell Type:{:<20}  Rate:{:.2%}     Num:{}".format(
                self.rare_type, self.cell_type_counts[0] / self.adata.n_obs, self.cell_type_counts[0])
            )

    def record_markers_found_and_MRR(self, selected_results: Union[tuple, List[tuple]]):
        if isinstance(selected_results, tuple):
            markers_found, MRR = self.cal_markers_found_and_MRR(selected_results)  # scalar
        elif isinstance(selected_results, list):
            markers_left, MRR_left = self.cal_markers_found_and_MRR(selected_results[0])
            markers_right, MRR_right = self.cal_markers_found_and_MRR(selected_results[1])
            markers_found, MRR = str([markers_left, markers_right]), str([MRR_left, MRR_right])  # list
        else:
            raise ValueError(f"selected_results should be tuple or list, but is {type(selected_results)}")
        self.markers_found.loc[self.current_fold, self.current_method] = markers_found
        self.MRR.loc[self.current_fold, self.current_method] = MRR

    def get_mask(self, all_genes: np.ndarray, single_selected_result: tuple) -> np.ndarray:
        """
        Get the mask that indicates which genes are selected.

        :param all_genes: all gene names
        :param single_selected_result: the result of selection
        :return: a bool array
        """
        selected_genes = single_selected_result[0]
        mask = np.isin(all_genes, single_selected_result[0])
        selected_markers_num = mask.sum()
        if selected_markers_num == 0:
            raise RuntimeError("No gene is selected!")
        if selected_markers_num < self.n_genes:
            msg = f"{self.current_method} selected {selected_markers_num} genes, not {self.n_genes}. "
            if selected_markers_num < selected_genes.shape[0]:
                msg += f"Some selected genes are not used: {np.setdiff1d(selected_genes, all_genes)}"
            warnings.warn(msg)
        return mask

    def cal_markers_found_and_MRR(self, single_selected_result: tuple):
        """
        Calculate number of selected marker genes and MRR if importances exist, otherwise only calculate markers num.

        :param single_selected_result: result from function 'select_features'
        :return: number of marker genes found, and MRR
        """
        selected_genes = single_selected_result[0]
        rank = False if len(single_selected_result) == 1 else True
        markers_found = np.intersect1d(self.adata.uns['markers'], selected_genes).shape[0]
        if rank:
            rank_list = np.argwhere(np.isin(selected_genes, self.adata.uns['markers']))
            if len(rank_list) == 0:
                warnings.warn("No marker gene is selected! MRR is set to 0.", RuntimeWarning)
                MRR = 0
            else:
                MRR = np.sum(1 / (rank_list + 1)) / rank_list.shape[0]
        else:  # len(selected_result) == 1
            warnings.warn("The method can't obtain gene importance! MRR is set to 0.", RuntimeWarning)
            MRR = 0
        return markers_found, MRR

    def fbeta_score(self, labels_true: np.ndarray, labels_pred: np.ndarray, beta=1.0):
        """
        In rare cell type detection, f-score becomes a harmonic average of P and R when beta == 1.0.

        :param labels_true: category annotation
        :param labels_pred: cluster annotation
        :param beta: the weighted hyperparameter
        :return: f-measure
        """
        assert labels_true.shape[0] == labels_pred.shape[0], \
            ValueError(
                f"length of labels_true({labels_true.shape[0]} and length of labels_pred({labels_pred.shape[0]}) "
                f"are inconsistent.")
        rare_type_mask = labels_true == self.rare_type
        F_score = []
        for cluster in np.unique(labels_pred[rare_type_mask]):  # clusters that rare cells are divided to
            cluster_mask = labels_pred == cluster
            cluster_true = labels_true[cluster_mask]
            # TP, P, R, F-beta
            n_true_positive = np.sum(cluster_true == self.rare_type)
            precision = n_true_positive / np.sum(cluster_mask)
            recall = n_true_positive / np.sum(rare_type_mask)
            F_score.append((beta ** 2 + 1) * precision * recall / (beta ** 2 * precision + recall))
        return max(F_score)

    def BCubed_score(self, labels_true: np.ndarray, labels_pred: np.ndarray, beta=1.0):
        """
        In rare cell type detection, BCubed_score is better than f-measure

        :param labels_true: category annotation
        :param labels_pred: cluster annotation
        :param beta: the weighting hyperparameter
        :return: BCubed score
        """
        rare_type_mask = labels_true == self.rare_type
        rare_cell_num = np.sum(rare_type_mask)
        precision, recall = [], []
        for cluster in np.unique(labels_pred[rare_type_mask]):  # clusters that rare cells are divided to
            cluster_mask = labels_pred == cluster
            cluster_true = labels_true[cluster_mask]

            n_true_positive = np.sum(cluster_true == self.rare_type)
            precision.append(n_true_positive ** 2 / np.sum(cluster_mask))  # sum of precision in this cluster
            recall.append(n_true_positive ** 2 / rare_cell_num)  # sum of recall in this cluster

        ave_precision, ave_recall = np.sum(precision) / rare_cell_num, np.sum(recall) / rare_cell_num
        fbeta_BCubed = (beta ** 2 + 1) * ave_precision * ave_recall / (beta ** 2 * ave_precision + ave_recall)
        return fbeta_BCubed

    def cmpt_time_start(self):
        """
        For python methods, start counting computation time

        :return: None
        """
        if self.save_cmpt_time:
            if self.start_time != 0:
                raise RuntimeError(f"start time should be 0 (int), not {self.start_time}.")
            else:  # start_time has been reset
                self.start_time = time.time()

    def cmpt_time_end(self):
        """
        For python methods, end counting computation time

        :return:
        """
        if self.save_cmpt_time:
            seconds = np.round(time.time() - self.start_time, decimals=5)
            self.computation_time.loc[self.current_fold, self.current_method] += seconds
            if '+' in self.current_method:
                print(f"base method {self.base_methods[self.count_base_method]} costs {seconds} seconds.")
                if self.count_base_method < len(self.base_methods):
                    self.count_base_method += 1
                    self.start_time = 0
                    if self.count_base_method == len(self.base_methods):
                        self.save_cmpt_time = False
            else:
                print(f"gene selection costs {seconds} seconds.")
                self.save_cmpt_time = False
                self.start_time = 0

    def record_cmpt_time_from_csv(self):
        """
        For R methods, read computation time

        :return: None
        """
        if self.save_cmpt_time:
            if '+' in self.current_method:  # ensemble learning
                time_path = f'tempData/{self.data_name}_time_{self.base_methods[self.count_base_method]}.csv'
                seconds = np.round(np.loadtxt(time_path, skiprows=1, usecols=[1], delimiter=',').tolist(), decimals=5)
                if isinstance(seconds, (float, int)):
                    print(f"base method {self.base_methods[self.count_base_method]} costs {seconds} seconds.")
                    self.computation_time.loc[self.current_fold, self.current_method] += float(seconds)
                    if self.count_base_method < len(self.base_methods):
                        self.count_base_method += 1
                        if self.count_base_method == len(self.base_methods):
                            self.save_cmpt_time = False
                else:
                    raise TypeError(f"{seconds} with type {type(seconds)} can't be converted to float type.")
            else:
                time_path = f'tempData/{self.data_name}_time_{self.current_method}.csv'
                seconds = np.round(np.loadtxt(time_path, skiprows=1, usecols=[1], delimiter=',').tolist(), decimals=5)
                if isinstance(seconds, (float, int)):
                    print(f"gene selection costs {seconds} seconds.")
                    self.computation_time.loc[self.current_fold, self.current_method] += float(seconds)
                    self.save_cmpt_time = False
                else:
                    raise TypeError(f"{seconds} with type {type(seconds)} can't be converted to float type.")

    def record_metrics_from_dict(self, metric_dict: Union[dict, List[dict]]):
        """
        Read evaluation result (dict or list of dict) and record

        :param metric_dict: evaluation result
        :return: None
        """
        if isinstance(metric_dict, dict):
            for key in metric_dict:
                getattr(self, key).loc[self.current_fold, self.current_method] = metric_dict[key]
        elif isinstance(metric_dict, list):
            half_dict = {key: str([metric_dict[0][key], metric_dict[1][key]]) for key in metric_dict[0].keys()}
            for key in half_dict.keys():
                getattr(self, key).loc[self.current_fold, self.current_method] = half_dict[key]
        else:
            raise TypeError(f"metric_dict with type {type(metric_dict)} is an invalid argument.")
        if self.current_method == self.methods[-1]:
            self.current_fold += 1

    def handle_timeout(self):
        """
        This function is only used in 'evaluate_assign_methods'.

        :return: None
        """
        print(f"{self.current_method} reached maximum computation time.")
        for metric in ['MRR', 'markers_found', 'singlecellnet_F1', 'singleR_F1',
                       'singlecellnet_F1_rare', 'singleR_F1_rare', 'seurat_ARI', 'sc3_ARI',
                       'seurat_F1_rare', 'sc3_F1_rare']:
            if hasattr(self, metric):
                getattr(self, metric).loc[self.current_fold, self.current_method] = 0
        if assign_cfg.method_lan[self.current_method] == 'python':  # actually only scGeneFit
            self.cmpt_time_end()
        else:
            self.computation_time.loc[self.current_fold, self.current_method] += exp_cfg.max_timeout
        if self.current_method == self.methods[-1]:
            self.current_fold += 1

    def summary(self, show=False, save=False):
        """
        print summary of evaluation

        :param show: whether to show summary
        :param save: whether to save the summary
        :return: summary
        """
        if self.task == 'assign':
            index = ['MRR', 'markers_found', 'computation_time', 'singlecellnet_F1', 'singleR_F1', 'itclust_F1']
            if hasattr(self, 'rare_type'):
                index += ['singlecellnet_F1_rare', 'singleR_F1_rare', 'itclust_F1_rare']
        else:
            index = ['MRR', 'markers_found', 'computation_time', 'seurat_ARI', 'sc3_ARI', 'cidr_ARI']
            if hasattr(self, 'rare_type'):
                index += ['seurat_F1_rare', 'sc3_F1_rare', 'cidr_F1_rare']
        summary = pd.DataFrame(data=np.zeros((len(index), len(self.methods))), index=index, columns=self.methods)
        for idx in index:  # .mean(axis=0)
            if hasattr(self, idx):
                summary.loc[idx, :] = getattr(self, idx).applymap(
                    lambda x: x if isinstance(x, float) else np.mean(eval(x))).mean(axis=0)
        if show:
            print('\n' * 3)
            head(f'summary of evaluation on {self.data_name} dataset')
            print(summary)
        if save:
            summary.to_csv(exp_cfg.record_path + '-'.join([self.data_name, self.task, str(self.n_genes)]) + '.csv')
        return summary

    def save(self):
        """
        Save this object to records/

        :return: None
        """
        self.adata = None  # do not save anndata
        with open(self.save_name, 'wb') as f:
            pickle.dump(self, f)
        print(f"{(datetime.datetime.now() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')}: "
              f"Finished and saved record to {self.save_name}\n\n\n")  # UTC + 8 hours = Beijing time
