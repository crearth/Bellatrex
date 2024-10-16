
import warnings
import numpy as np
import pandas as pd

import sklearn
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
from sklearn.decomposition import PCA
from sklearn.tree import _tree # to check things like _tree.TREE_UNDEFINED

import sksurv
from sksurv.ensemble import RandomSurvivalForest
from sksurv.tree import SurvivalTree

import matplotlib as mpl
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm
from matplotlib.ticker import FuncFormatter

from .wrapper_class import EnsembleWrapper


def get_auto_setup(y_test):

    if isinstance(y_test, np.recarray) and len(y_test.dtype.names) == 2:
        return "survival" # single output only, for now
    else:
        y_test = np.array(y_test)
        if y_test.ndim == 1:
            unique_vals = np.unique(y_test).tolist()
            if len(unique_vals) <= 2:
                return "binary"
            else:
                return "regression"
        elif y_test.ndim == 2: # multi-output case
            unique_vals = np.unique(y_test.ravel()).tolist()
            if len(unique_vals) <= 2:
                return "multi-label"
            else:
                return "multi-target"
        else:
            raise ValueError(f'Unexpected case, shape: {y_test.shape}')

def concatenate_helper(y_pred, y_local_pred, axis=0):

    if y_pred.shape[0] == 0: #if still empty (no rows added)

        # Initialize final_array columns based on the first new_array
        if y_local_pred.ndim == 2: #if output is 2D array
            y_pred = np.empty((0, y_local_pred.shape[1])) #for (n_samples, n_outputs)
        if y_local_pred.ndim == 1: #if 1D array instead
            y_pred = np.empty(0) #for (n_samples,)

    # concatenate along first axis (works in any case)
    return np.concatenate((y_pred, y_local_pred), axis=axis)


def predict_helper(clf, X):
    '''
    INPUTS:
    - clf: either a RandomForestClassfier, RandomForestRegressor,
     a RandomSurvivalForest, or a EnsembleWrapper instance generated by Bellatrex
    - input data (usually the one where the model has been trained on)

    '''

    # GOAL HERE is to store tree predictions in a consistent format.
    # It should look as (n_samples, n_outputs) for multi-output,
    # and (n_samples,) for single output

    # WHEN EXTENDING TO other tree-based learners (e.g. ExtraTrees) consider
    # checking for n_outputs and n_classes instead of check with isinstance

    if isinstance(clf, (RandomForestClassifier, DecisionTreeClassifier)) and clf.n_outputs_ == 1:
        return clf.predict_proba(X)[:,1]
    if isinstance(clf, (RandomForestClassifier, DecisionTreeClassifier)) and clf.n_outputs_ > 1:
        return np.array(clf.predict_proba(X))[:,:,1].T
    if isinstance(clf, (RandomForestRegressor, DecisionTreeRegressor,
                          RandomSurvivalForest, SurvivalTree)):
        return clf.predict(X)
    elif isinstance(clf, (EnsembleWrapper, EnsembleWrapper.Estimator)):
        ypred = clf.predict(X)
        if ypred.shape[1] == 1: # if output is of shape: (n_samples, 1)
            return ypred.squeeze(axis=1) #consistency with sklearn output shape: (n_samples,)
        #  otherwise if output is of shape: (n_samples, n_outputs) keep as it is
    else:
        raise ValueError('Tree learner \'{clf.__class__.__name__}\' not recognized, or not implemented yet')


def trail_pretty_digits(value, tot_digits):
    '''
    Based on the order of magnitude of the value under consideration,
    leave an appropriate number of decimals. The higher the (absolute)
    value of the number, the fewer decimals.
    '''
    # take a celining, without using the math module
    if np.abs(value) < 0.03:
        front_digits = 0 #we will use scientific notation in this case so ignore negative amounts
    else:
        front_digits = round(np.log10(np.abs(value))+0.5)
    front_digits = max(0, front_digits) # we will use scientific notation in this case

    return tot_digits - front_digits

def string_to_pretty_digits(val_str, digits_single=4):

    if val_str.count(" ") > 1: # multiple numbers stored as strings, here
        raise ValueError(f'Expected single value as a string, found: \'{val_str}\' instead')

    elif val_str.count(" ") == 1:
        val_str.replace(" ", "")

    if 'e' in val_str:
        base, exponent = val_str.split('e')
        exponent = 'e' + exponent
    else: # leave exponent part empty:
        base, exponent = val_str, ''
    if '.' in base: #if decimal number:
        int_part, dec_part = base.split('.')
        dec_part = dec_part[:max(0, digits_single - len(int_part))] #crop some digits from here
        cropped = f"{int_part}.{dec_part}"
    else: #big, integer number
        cropped = base
        warnings.warn(f'Encountered big number with > {digits_single} digits, in non-exponenetial notation')
    return cropped + exponent

def frmt_pretty_print(y_pred, digits_single=4, digits_vect=3) -> str:
    '''
    Some pretty formatting from single values and np.arrays.
    Outputs values as a (single) string
    '''
    assert digits_single >= 1
    assert digits_vect >= 1
    y_pred_str = None #initialized, it's supposed to be manipulated inside the function

    # if 2-d, the only acceptable option is that it is a nested 1-d vector
    if isinstance(y_pred, np.ndarray) and y_pred.ndim == 2:
        if y_pred.shape[0] > 1:
            raise ValueError(f'Output vector must be 1d, or with shape (1,p), found shape {y_pred.shape}')
        y_pred = y_pred.ravel() # unnest the vecotr to get a 1-d one in case

    # case if it is a 1-d vector of shape (n,):
    if isinstance(y_pred, np.ndarray) and y_pred.shape[0] > 1:

        count_extremes = np.sum(np.abs(y_pred) < 10**(-digits_vect+2)) +\
                         np.sum(np.abs(y_pred) > 10**(digits_vect))
        prop_extremes = count_extremes/len(y_pred)

        if prop_extremes < 0.5:
            y_pred_str = ", ".join(f"{val:.{trail_pretty_digits(val, digits_vect)}f}"
                                for val in y_pred)
        else: # majority of numbers is very small or very big: use expon notation, drop one decimal
            y_pred_str = ", ".join(f"{val:.{digits_vect-1}e}"
                                for val in y_pred)

    if isinstance(y_pred, np.ndarray) and y_pred.size == 1:
        y_pred = float(y_pred) #transform to float and go to next case:

    if isinstance(y_pred, (int, float)): # case for float and for singel-value array of shape (1,)

        is_extreme = np.abs(y_pred) < 10**(-digits_single+2) or np.abs(y_pred) > 10**(digits_single)
        if not is_extreme:
            y_pred_str = f"{y_pred:.{trail_pretty_digits(y_pred, digits_single)}f}"
        else: # majority of numbers is very small or very big: use expon notation, drop one decimal
            y_pred_str = f"{y_pred:.{digits_single-1}e}"

    if isinstance(y_pred, str):
        warnings.warn('y_pred is already a string. You might be calling the formatting function twice')
    if y_pred_str is None:
        # then none of the previous cases was encoutnered (ideally use elif but it's tricky)
        raise ValueError(f"Format for y_pred not recognized, or its shape is unusual."
                         f"\nFound {type(y_pred)}: {y_pred}")

    return y_pred_str


def return_partial_preds(clf_i):

    if isinstance(clf_i, sklearn.tree.DecisionTreeClassifier) and clf_i.n_outputs_ == 1:
        partials = clf_i.tree_.value[:,0,:] # now take average
        partial_preds = partials[:,1] / (partials[:,0] + partials[:,1])

    elif isinstance(clf_i, sklearn.tree.DecisionTreeClassifier) and clf_i.n_outputs_ > 1:
        partials = clf_i.tree_.value
        partial_preds = partials[:,:,1] / (partials[:,:,0] + partials[:,:,1])

    elif isinstance(clf_i, sklearn.tree.DecisionTreeRegressor) and clf_i.n_outputs_ == 1:
        partial_preds = clf_i.tree_.value.ravel() # DOUBLE CHECK!

    elif isinstance(clf_i, sklearn.tree.DecisionTreeRegressor) and clf_i.n_outputs_ > 1:
        partial_preds = clf_i.tree_.value.squeeze(axis=-1) #(n,p,1) to (n,p)

    elif isinstance(clf_i, SurvivalTree):
        # clf_i.tree_.value: np array of [node, time, [H(node), S(node)]]
        #                                              ^idx 0   ^idx 1
        # We imitate the .predict function of the SurvivalTree:
        partial_preds = np.sum(clf_i.tree_.value[:,clf_i.is_event_time_, 0], axis=1)

    elif isinstance(clf_i, EnsembleWrapper.Estimator):
        if clf_i.n_outputs_ == 1:
            partial_preds =  clf_i.tree_.value.ravel() # .ravel seems to do the needed formatting
        else:
            partial_preds =  clf_i.tree_.value
    else:
        raise ValueError('Tree learner not recognized, or not implemented')

    return partial_preds


def used_feature_set(clf_i, feature_names, sample):

    unique_features = []
    # tested for RandomForestClassifier and EnsembleWrapper (binary set-up)

    node_indicator_csr = clf_i.decision_path(sample.values)  # sparse matrix (1, n_nodes)
    feature_idx_per_node = clf_i.tree_.feature  # array (n_nodes, )

    node_index = node_indicator_csr.indices[
        node_indicator_csr.indptr[0]: node_indicator_csr.indptr[1]
    ]  # csr matrix formatted in this way

    for node_id in node_index[:-1]:  # internal nodes (exclude leaf)
        feature_node_id = feature_idx_per_node[node_id]
        feature_name = feature_names[feature_node_id]
        if feature_name not in unique_features:  # add element to list if not in there yet
            unique_features.append(feature_name)

    return unique_features


def _check_in_features(feature_names, sample):

    if len(feature_names) != sample.shape[1]:
        raise ValueError(f"Found {len(feature_names)} features, whereas the sample to explain"
                            f" has shape {sample.shape}. expected {sample.shape[1]} features instead")
    else:
        pass

def colormap_from_str(colormap):
    '''
    Function for the user to customize the colorbmap
    '''
    if colormap is None:
        cmap_output = plt.cm.get_cmap('RdYlBu_r') # default choice. Other possible default could be "viridis"

    elif isinstance(colormap, LinearSegmentedColormap):
        cmap_output = colormap

    elif isinstance(colormap, str):
        if colormap not in plt.colormaps():
            raise ValueError(f'Provided string {colormap} is not a recognized LinearSegmenetedColormap.'
                             f' Check list by typing plt.colormaps()')
        else:
            cmap_output = plt.cm.get_cmap(colormap)
    else:
        raise ValueError('Provided colormap has to be either a LinearSegmenetedColormap,'
                         f' or a recognized string. Found {type(colormap)} instead')
    return cmap_output


def rule_print_inline(clf_i, sample, weight=None, max_features_print=12):

    ''' sample is a pd.Series or a single-row pd.DataFrame?? '''
    ## consider treating it as a numpy array
    if isinstance(sample, np.ndarray):
        sample = pd.DataFrame(sample)
        sample.columns = [f"X_{i}" for i in range(len(sample.columns))]

    # node_indicator = clf_i.decision_path(sample)
    node_indicator_csr = clf_i.decision_path(sample.values)
    # node_weights = clf_i.tree_.n_node_samples/(clf_i.tree_.n_node_samples[0])
    children_left = clf_i.tree_.children_left
    children_right = clf_i.tree_.children_right
    feature = clf_i.tree_.feature
    threshold = clf_i.tree_.threshold

    is_traversed_node = node_indicator_csr.indices[
        node_indicator_csr.indptr[0] : node_indicator_csr.indptr[1]
    ] # csr matrix formatted in this way

    unique_features = used_feature_set(clf_i, sample.columns, sample)

    # Print only the relevant features with max 4 digits
    # take care of selection of sample columns so that it stays as pd.DataFrame:
    unique_features_formatted = sample[unique_features].apply(lambda col: col.map(lambda x: frmt_pretty_print(x, 4)))


    if len(unique_features) <= max_features_print:
        print('#'*22, '   SAMPLE   ', '#'*22)
        print(unique_features_formatted.to_string(col_space=4))
        print('#'*58)
    else:
        print('#'*58)
        print('Too many features are used in the extracted rules, therefore we', end='')
        print('skip the printing. \n Increase the max_features_print parameter in case')
        print('#'*58)


    partial_preds = return_partial_preds(clf_i)


    if weight is None:
        print(f'Baseline prediction: {frmt_pretty_print(partial_preds[0])}')
    else:
        print(f'Baseline prediction: {frmt_pretty_print(partial_preds[0])} \t (weight = {weight:.2f})')

    for node_id in is_traversed_node[:-1]: #internal nodes (exclude leaf)
        # continue to the next node if it is a leaf node

        # check if value of the split feature for sample 0 is below threshold
        if sample.values[0, feature[node_id]] <= threshold[node_id]:
            threshold_sign = "<="
            next_child = children_left[node_id]
        else:
            threshold_sign = "> "
            next_child = children_right[node_id]

        print(
            f"node {node_id:3}: "
            f"{sample.columns[feature[node_id]]:>8} {threshold_sign} {threshold[node_id]:5>.2f} "
            f"{'(' + sample.columns[feature[node_id]]:>9} = {sample.values[0, feature[node_id]]:5>.2f})"
            f"  -->  {frmt_pretty_print(partial_preds[next_child])}"
        )


    print(
        f"leaf {is_traversed_node[-1]:4}: predicts: "
        f"{frmt_pretty_print(partial_preds[is_traversed_node[-1]])}"
        )


def rule_to_file(clf_i, sample, rule_weight, max_features_print, f):

    # leaf_print = predict_helper(clf_i, sample.values)
    partial_preds = return_partial_preds(clf_i)

    feature_names = sample.columns

    def recurse_print(node, depth, tree_, sample, feature_names, is_traversed_node, f):
        indent = "  " * depth

        if tree_.feature[node] != _tree.TREE_UNDEFINED: #if feature is not undefined != -2
            name = feature_names[tree_.feature[node]]
            threshold = tree_.threshold[node]

            if is_traversed_node[node] == 1 and sample[tree_.feature[node]] <= threshold:
                is_traversed_node[node] = 0 #otherwise it will keep printing this rulesplit
                child_node = tree_.children_left[node]
                threshold_str = frmt_pretty_print(threshold, 6)
                partial_preds_str = frmt_pretty_print(partial_preds[child_node], 6)
                f.write(f"node.{node:4}: {indent} {name} <= {threshold_str}  --> {partial_preds_str}\n")

                recurse_print(child_node, depth + 1, tree_, sample,
                              feature_names, is_traversed_node, f)

            if is_traversed_node[node] == 1 and sample[tree_.feature[node]] > threshold:
                is_traversed_node[node] = 0
                child_node = tree_.children_right[node]
                threshold_str = frmt_pretty_print(threshold, 6)
                partial_preds_str = frmt_pretty_print(partial_preds[child_node], 6)
                f.write(f"node.{node:4}: {indent} {name} >  {threshold_str}  --> {partial_preds_str}\n")

                recurse_print(child_node, depth + 1, tree_, sample,
                              feature_names, is_traversed_node, f)

        else: # if feature split is == _tree.TREE_UNDEFINED (that is, -2), then we are in a leaf
            if is_traversed_node[node] == 1:
                partial_preds_str = frmt_pretty_print(partial_preds[node], 6)
                f.write(f"leaf.{node:4}: {indent}returns {partial_preds_str}\n")

    tree_structure = clf_i.tree_
    unique_features = used_feature_set(clf_i, feature_names, sample)

    # Take care of selection of sample columns so that
    # it stays in the pd.DataFrame format. This works:
    # store values with at most 6 digits in the file ( whjta baout col_space = 5 though?)
    unique_features_formatted = sample[unique_features].apply(lambda col: col.map(lambda x: frmt_pretty_print(x, 6)))


    if len(unique_features) <= max_features_print:
        f.write('#'*24 + '  SAMPLE  ' + '#'*24 + '\n')
        f.write(unique_features_formatted.to_string(col_space=5)+ '\n')
    else:
        f.write('#'*58 + '\n')

    f.write('#'*18 + f'   RULE WEIGHT: {rule_weight:.2f} '+ '#'*18 + '\n')
    f.write(f'Baseline prediction: {frmt_pretty_print(partial_preds[0], 6)}\n')

    is_traversed_node = clf_i.decision_path(sample.values).toarray()[0]
    sample = sample.to_numpy().reshape(-1) #from single column to single line
    ## and here most of the printing is done (recursive calls)
    recurse_print(0, 0, tree_structure, sample, feature_names, is_traversed_node, f) #feature_name list missing?



def rule_to_code(clf_i, traversed_nodes, sample, full_save_name):

    leaf_print = predict_helper(clf_i, sample.values)

    tree_ = clf_i.tree_
    feature_names = sample.columns # it's a pd.DataFrame by now

    feature_name = [
        feature_names[i] if i != _tree.TREE_UNDEFINED else "undefined!"
        for i in tree_.feature]

    intervals = {feat : [-np.inf, np.inf] for feat in feature_names}

    if full_save_name is not None:
        with open(full_save_name, 'w+') as f:
            f.write("###### SAMPLE to explain ######\n")

            for i,k in zip(feature_names, range(len(feature_names))):
                f.write(f"{str(i):13}: {str(sample[k]):7} \n")

            f.write("\n###############################\n")

            sample = sample.to_numpy().reshape(-1) #from single column to single line

            def recurse(node, depth, sample, intervals):
                indent = "  " * depth
                if tree_.feature[node] != _tree.TREE_UNDEFINED: #if feature is not undefined (??)
                    name = feature_name[node]
                    threshold = tree_.threshold[node]
                    if traversed_nodes[node] == 1 and sample[tree_.feature[node]] <= threshold:
                        intervals[name][1] = threshold # reduce feature upper bound
                        traversed_nodes[node] = 0
                        f.write(f"node.{name}:{indent} if {name} <= {threshold}\n")

                    recurse(tree_.children_left[node], depth + 1, sample, intervals)

                    if traversed_nodes[node] == 1 and sample[tree_.feature[node]] > threshold:
                        intervals[name][0] = threshold # increase feature lower bound
                        traversed_nodes[node] = 0
                        f.write(f"node {name}:{indent} if {name} > {threshold}\n")
                    recurse(tree_.children_right[node], depth + 1, sample, intervals)
                else: #it is undefined, it is therefore a leaf (?)
                    if traversed_nodes[node] == 1:
                        #print("leafnode.{}: {}return {}".format(node, indent, leaf_print2)) #tree_.value[node].ravel()
                        f.write(f"leafnode.{name}:{indent} returns {leaf_print}\n")
                        f.write(f"predicted:{leaf_print}\n")
            recurse(0, 1, sample, intervals)
            f.close()



def rule_to_code_and_intervals(clf_i, scenario, traversed_nodes, sample, feature_names, full_save_name):


    leaf_print = predict_helper(clf_i, sample)

    tree_ = clf_i.tree_
    feature_name = [
        feature_names[i] if i != _tree.TREE_UNDEFINED else "undefined!"
        for i in tree_.feature]

    intervals = {feat : [-np.inf, np.inf] for feat in feature_names}

    if full_save_name is not None:
        with open(full_save_name, 'w+') as f:
            f.write("###### SAMPLE to explain ######\n")

            for i,k in zip(feature_names, range(len(feature_names))):
                f.write(f"{str(i):13}: {str(sample[k]):7} \n")

            f.write("\n###############################\n")

            sample = sample.to_numpy().reshape(-1) #from single column to single line

            def recurse(node, depth, sample, intervals):
                indent = "  " * depth
                if tree_.feature[node] != _tree.TREE_UNDEFINED:
                    name = feature_name[node]
                    threshold = tree_.threshold[node]
                    if traversed_nodes[node] == 1 and sample[tree_.feature[node]] <= threshold:
                        intervals[name][1] = threshold # reduce feature upper bound
                        traversed_nodes[node] = 0
                        f.write(f"node.{node}:{indent} if {name} <= {threshold}\n")

                    recurse(tree_.children_left[node], depth + 1, sample, intervals)

                    if traversed_nodes[node] == 1 and sample[tree_.feature[node]] > threshold:
                        intervals[name][0] = threshold # increase feature lower bound
                        traversed_nodes[node] = 0
                        f.write(f"node.{node}:{indent} if {name} > {threshold}\n")
                    recurse(tree_.children_right[node], depth + 1, sample, intervals)
                else: #it is undefined, it is therefore a leaf (?)
                    if traversed_nodes[node] == 1:
                        f.write(f"leafnode.{node}:{indent} return {leaf_print}\n")
                        f.write(f"predicted:{leaf_print}\n")
            recurse(0, 1, sample, intervals)
            f.close()

    if full_save_name is not None:
        with open(full_save_name.split(".")[0] + "-simplif." + full_save_name.split(".")[-1], 'w+') as f:
            f.write("###### SAMPLE to explain ######\n")

            for i,k in zip(feature_names, range(len(feature_names))):
                f.write(f"{str(i):10}: {str(sample[k]):7}\n")

            f.write("\n###### final intervals ########\n")

            for item in intervals:
                if intervals[item][0] != -np.inf or intervals[item][1] != np.inf:
                    f.write(
                        f"{intervals[item][0]:6} < {str(item).center(8)} "
                        f"<= {intervals[item][1]:6} \n")
            f.close()

            with open(full_save_name) as f: #printing tree-rule structure on console
                print(f.read())

            print("###############################") #split between tree rule print and leaf interval representation

            with open(full_save_name.split(".")[0] + "-simplif." + full_save_name.split(".")[-1]) as f:
                print(f.read()) #printing (simplified) leaf structure on console


def custom_axes_limit(bunch_min_value, bunch_max_value, force_in, is_binary):

    if force_in is None:
        force_in  = np.nan # so that it can be ignored in later computations (min, max)

    v_min = min(bunch_min_value, force_in)
    v_max = max(bunch_max_value, force_in)

    if is_binary:
        # combat counterintuitive colouring when predictions are very confident
        # if all predicitons are very low, they are all mapped in the lower part of the ColorMap
        v_min = min(v_min, 0.7) # v_min never above 0.7
        # if all predicitons are very high, they are all mapped in the upper part of the ColorMap
        v_max = max(v_max, 0.3) # v_max never below 0.3

    # add a bit of extra spacing on the extremes, to avoid the case v_min = v_max
    # still possible if e.g. v_min ~ v_max ~ 0.5
    v_min = v_min-(v_max-v_min)*0.02
    v_max = v_max+(v_max-v_min)*0.02+0.005

    return v_min, v_max

def custom_formatter(x, pos): # pos paramter to comply with expected signature
    '''
    Custom formatter function, useful for the colorabar
    '''
    if 1e-2 <= np.abs(x) < 1:
        return f"{x:.2f}"  # 2 decimal digits for numbers between -1 and 1, with asb value >= 0.01
    elif 1 <= np.abs(x) < 10:
        return f"{x:.1f}"  # 1 decimal digit for numbers with 1 significant digit before decimal point
    elif 10 <= np.abs(x) < 100:
        return f"{x:.0f}"  # round to nearest integer for numbers with 2 digits before decimal point
    else: # np.abs(x) < 1e-2 or np.abs(x) > 100 (very big or very small numbers)
        return f"{x:.1e}"  # Scientific notation with 2 significant digits (x.y1e__)


    ## LocalMethod inputs: plot_data_bunch, plot_kmeans, tuned_method, self.clf.n_outputs_

def plot_preselected_trees(plot_data_bunch, kmeans, tuned_method,
                           base_font_size=12, show_ax_ticks="auto",
                           colormap=None):

    # TODO: add transparency with parameter alpha (default = 1: fully opaque. Accept in [0,1])
    # accept up to 2 degrees of opaqueness: one for normal dots and one for the selected candidates

    small_size = 40
    big_size = 220

    if show_ax_ticks == "auto":
        show_ax_ticks = False if base_font_size > 15 else True

    #PCA to 2 dimensions for projected trees
    #(original proj dimension can be > 2)
    PCA_fitted = PCA(n_components=2).fit(plot_data_bunch.proj_data)
    plottable_data = PCA_fitted.transform(plot_data_bunch.proj_data)  # (lambda,2)

    centers = PCA_fitted.transform(kmeans.cluster_centers_)
    class_memb = kmeans.labels_

    custom_gridspec = {'width_ratios': [3, 0.2, 3, 0.2]}

    fig = plt.figure(figsize=(10, 4.5))
    axes = fig.subplots(1, 4, gridspec_kw=custom_gridspec)
    # fig, axes = plt.subplots(1, 4, figsize=(10, 4.5),
    #                          gridspec_kw=custom_gridspec)

    # fig.subplots_adjust(top=0.85)
    # fig.tight_layout()

    # conditional sizes for trees and candidate trees:
    is_final_candidate = [plot_data_bunch.index[i] in tuned_method.final_trees_idx
                          for i in range(len(plot_data_bunch.index))]

    #####   LEFT PLOT (cluster memberships)   #####

    for i, txt in enumerate(centers):  # plot cluster centers
        axes[0].annotate(i+1, (txt[0], txt[1]), # old .annotate(i+1, centers[i,0], centers[i,1]),
                        bbox={"boxstyle": "circle", "color": "grey", "alpha": 0.6})


    x_normal = plottable_data[:,0][[not x for x in is_final_candidate]]
    y_normal = plottable_data[:,1][[not x for x in is_final_candidate]]
    color_normal = class_memb[[not x for x in is_final_candidate]]

    x_candidate = plottable_data[:,0][is_final_candidate]
    y_candidate = plottable_data[:,1][is_final_candidate]
    color_candidate = class_memb[is_final_candidate]

    axes[0].scatter(x_normal, y_normal,
               c=color_normal,
               cmap=None,
               s=small_size,
               marker="o",
               edgecolors=(1, 1, 1, 0.5))

    axes[0].scatter(x_candidate, y_candidate,
               c=color_candidate,
               cmap=None,
               s=big_size,
               marker="*",
               edgecolors="black")

    axes[0].set_xlabel("PC1", fontdict={'fontsize': base_font_size-1})
    axes[0].set_ylabel("PC2", fontdict={'fontsize': base_font_size-1})

    axes[0].axis('equal') # is it even a good idea? We will see
    axes[0].set_title('Cluster membership', fontdict={'fontsize': base_font_size+1})

    # create the map for segmented colorbar (axes[1]: left colorbar)
    cmap = plt.cm.viridis  # keep default colormap for clustering plot
    cmaplist = [cmap(i) for i in range(cmap.N)]
    cmap_left = mpl.colors.LinearSegmentedColormap.from_list('Custom cmap', cmaplist, cmap.N)

    # define the bins and normalize
    freqs = np.bincount(class_memb)
    if np.min(freqs) == 0:
         raise KeyError("There are empty clusters, the scatter and colorbar would differ in color shade")
    norm_bins = list(np.cumsum(freqs))
    norm_bins.insert(0, 0)

    if len(norm_bins) == 2:  # color gradient is off, add artificial bin
        # this will create an empty artificial cluster later on, that will be dropped
        norm_bins.insert(-1, norm_bins[1])

    # scatterplot color does not scale correctly if there are empty classes!
    # transform list to array (ticks location needs arithmentic computation)
    norm_bins = np.array(norm_bins)

    # create label names
    labels = []
    for i in np.unique(class_memb):
        labels.append("cl.{:d}".format(i+1))

    # normalizing color, prepare ticks, labels
    norm = mpl.colors.BoundaryNorm(norm_bins, cmap_left.N)
    tickz = norm_bins[:-1] + (norm_bins[1:] - norm_bins[:-1]) / 2

    if tickz.max() == norm_bins.max(): #artificial empty cluster somewhere: drop
        tickz = tickz[:-1] # drop last tick at top of colorbar

    # colorab on axis 2 out of 4.
    cb = mpl.colorbar.Colorbar(axes[1], cmap=cmap_left, norm=norm,
        spacing='proportional', ticks=tickz, boundaries=norm_bins,
        format='%1i')
        #label="cluster membership")
    cb.ax.set_yticklabels(labels)  # vertically oriented colorbar
    cb.ax.tick_params(labelsize=base_font_size-1) #ticks font size
    axes[1].yaxis.set_ticks_position('left')

    # User can customize the colorbar
    cmap_right = colormap_from_str(colormap)

    #####   RIGHT PLOT (predictions or losses)  #####

    # PREDICTIONS when single class output (SurvivalTree included)
    if tuned_method.clf.n_outputs_ == 1 or isinstance(tuned_method.clf,
                                        RandomSurvivalForest): # single output, color on predictions

        ### right figure scatterplot here (axes[2] and axes[3]):

        is_binary = False
        if isinstance(tuned_method.clf, sklearn.ensemble.RandomForestClassifier):
            is_binary = (tuned_method.clf.n_outputs_ == 1)
        elif isinstance(tuned_method.clf, EnsembleWrapper):
            is_binary = (tuned_method.clf.n_outputs_ == 1) and (tuned_method.clf.ensemble_class == 'RandomForestClassifier')


        v_min, v_max = custom_axes_limit(np.array(plot_data_bunch.pred).min(),
                                         np.array(plot_data_bunch.pred).max(),
                                         force_in=plot_data_bunch.rf_pred,
                                         is_binary=is_binary)

        norm_preds = mpl.colors.BoundaryNorm(np.linspace(v_min, v_max, 256),
                                             cmap_right.N)

        color_indeces = np.zeros(len(plot_data_bunch.pred)) #length = n_trees

        for i, bunch_pred in enumerate(plot_data_bunch.pred):
            # count number of values in norm_preds.boundaries that are less than the prediction
            color_indeces[i] = np.argmin([thresh <= bunch_pred
                                          for thresh in norm_preds.boundaries])


        # format as integers, for list comprehension
        color_indeces = [int(x+0.1) for x in color_indeces]

        real_colors = np.array([cmap_right(idx) for idx in color_indeces])

        axes[2].scatter(x_normal, y_normal,
                   c=real_colors[[not x for x in is_final_candidate]],
                   s=small_size, #cmap=cmap_right,
                   marker="o",
                   edgecolors=(1,1,1,0.5))

        axes[2].scatter(x_candidate, y_candidate,
                   c=real_colors[is_final_candidate],
                   s=big_size, #cmap=cmap_right,
                   marker="*",
                   edgecolors="black")

        axes[2].set_xlabel("PC1", fontdict={'fontsize': base_font_size-1})
        axes[2].yaxis.set_label_position("right")
        axes[2].set_ylabel("PC2", fontdict={'fontsize': base_font_size-1})
        #axes[2].yaxis.tick_right()
        axes[2].axis('equal')
        axes[2].set_title('Rule-path predictions', fontdict={'fontsize': base_font_size+1})

        # add color bar to the side
        pred_tick = np.round(float(tuned_method.local_prediction()), 3)

        cb2 = mpl.colorbar.Colorbar(axes[3], cmap=cmap_right, norm=norm_preds,
                                    format=FuncFormatter(custom_formatter),
                                    label="predicted: " + str(pred_tick))

        ## add to colorbar a line corresponding to Bellatrex prediction

        pred_lines = [float(x) for x in plot_data_bunch.pred]

        cb2.ax.plot([0, 1], [pred_lines]*2, color='grey',
                    linewidth=1)
        cb2.ax.plot([0.02, 0.98], [pred_tick]*2, color='black', linewidth=2.5,
                    marker="P")


        if isinstance(tuned_method.clf, sksurv.ensemble.RandomSurvivalForest):
            cb2.set_label("Cumul. Hazard: "+ str(pred_tick),
                          size=base_font_size-3)

        elif isinstance(tuned_method.clf, sklearn.ensemble.RandomForestClassifier):
            cb2.set_label("Pred. Prob:"+ str(pred_tick),
                          size=base_font_size-3)
        elif isinstance(tuned_method.clf, sklearn.ensemble.RandomForestRegressor):
            cb2.set_label("Pred. value:"+ str(pred_tick),
                          size=base_font_size-3)

        elif isinstance(tuned_method.clf, EnsembleWrapper):
            if tuned_method.clf.ensemble_class == 'RandomSurvivalForest':
                cb2.set_label("Cumul. Hazard: "+ str(pred_tick),
                              size=base_font_size-3)

            elif tuned_method.clf.ensemble_class == 'RandomForestClassifier':
                cb2.set_label("Pred. prob:"+ str(pred_tick),
                              size=base_font_size-3)
            elif tuned_method.clf.ensemble_class == 'RandomForestRegressor':
                cb2.set_label("Pred. value:"+ str(pred_tick),
                              size=base_font_size-3)
            else:
                raise ValueError(f'Case for EnsembleWrapper not recognized: {tuned_method.clf.ensemble_class}')
        else:
            raise ValueError(f'Model not recognized: {tuned_method.clf}')

    # LOSS  when multi-output predictions: plot distance from RF preds. The lower the better (blue)
    else:
        # adds padding betwwen v_min and v_max in case they coincide
        v_min, v_max = custom_axes_limit(np.array(plot_data_bunch.loss).min(),
                                          np.array(plot_data_bunch.loss).max(),
                                          force_in=np.nan,
                                          is_binary=False)

        norm_preds = BoundaryNorm(np.linspace(v_min, v_max, 256), cmap.N)

        final_candidate_loss = np.array(plot_data_bunch.loss)[is_final_candidate]
        normal_rule_loss = np.array(plot_data_bunch.loss)[[not x for x in is_final_candidate]]


        axes[2].scatter(x_normal, y_normal,
                   c=normal_rule_loss,
                   cmap=cmap_right,
                   norm=norm_preds,
                   s=small_size,
                   marker="o",
                   edgecolors=(1,1,1,0.5))

        axes[2].scatter(x_candidate, y_candidate,
                   c=final_candidate_loss,
                   cmap=cmap_right,
                   norm=norm_preds,
                   s=big_size,
                   marker="*",
                   edgecolors="black")

        axes[2].set_xlabel("PC1", fontdict={'fontsize': base_font_size-1})
        axes[2].yaxis.set_label_position("right")
        axes[2].set_ylabel("PC2", fontdict={'fontsize': base_font_size-1})
        #axes[2].yaxis.tick_right()
        axes[2].axis('equal')
        axes[2].set_title('Rule-path predictions', fontdict={'fontsize': base_font_size+1})


        cb2 = mpl.colorbar.Colorbar(axes[3], cmap=cmap_right, norm=norm_preds,
                                    label=str(tuned_method.fidelity_measure)+' loss')
        cb2.ax.plot([0, 1], [plot_data_bunch.loss]*2, color='grey', linewidth=1)


    # end indentation single-target vs multi-target case

    ticks_to_plot = axes[3].get_yticks()

    if np.abs(np.min(ticks_to_plot)) < 1e-3 and np.abs(np.max(ticks_to_plot)) > 1e-2:
        min_index = np.argmin(ticks_to_plot)
        ticks_to_plot[min_index] = 0
        axes[3].set_yticks(ticks_to_plot)

    axes[3].yaxis.set_major_formatter(FuncFormatter(custom_formatter))
    axes[3].minorticks_off()

    cb2.ax.tick_params(labelsize=base_font_size-3) #ticks font size

    if show_ax_ticks is False:
        axes[0].set_xticklabels([])
        axes[0].set_yticklabels([])
        axes[2].set_xticklabels([])
        axes[2].set_yticklabels([])

    return fig, axes