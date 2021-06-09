import datetime
from dateutil.relativedelta import relativedelta
import os

import feature_engine.missing_data_imputers as mdi
from feature_engine import categorical_encoders as ce
from feature_engine import variable_transformers as vt
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn import metrics
from sklearn import ensemble
from sklearn import linear_model
from sklearn import model_selection
from sklearn import tree     # Árvore de Decisão
from sklearn import ensemble # Random Forest
import sqlalchemy
from tqdm import tqdm
import xgboost as xgb        # XGBoost

# Create constants
DATA_PATH = 'data/olist_dsa.db'
QUERY_PATH = 'sql/Script_ABT_olist_dtref_safra_20200818.sql'
model_path = 'models/'

primeira_safra = "2018-05-01"
ultima_safra = "2018-05-01"

def get_abt(DATA_PATH, QUERY_PATH, primeira_safra, ultima_safra):
    ''' Function to create ABT for source tables
    '''
    with open( QUERY_PATH, 'r' ) as open_file:
        query = open_file.read()

    primeira_safra_datetime = datetime.datetime.strptime(primeira_safra,
                                                         "%Y-%m-%d")
    ultima_safra_datetime = datetime.datetime.strptime(ultima_safra, 
                                                       "%Y-%m-%d")

    con = sqlalchemy.create_engine( 'sqlite:///' + DATA_PATH )

    def exec_etl(queries, con):
        for q in tqdm(queries.split(";")[:-1]):
            con.execute(q)

    while primeira_safra_datetime <= ultima_safra_datetime:
        safra = primeira_safra_datetime.strftime("%Y-%m-%d")
        query_exec = query.format( data_ref=safra )
        primeira_safra_datetime = (primeira_safra_datetime
                                   + relativedelta(months=+1))

    if safra == primeira_safra:
        query_exec += f'''DROP TABLE IF EXISTS TB_ABT;
                            CREATE TABLE TB_ABT AS
                            SELECT '{safra}' as dt_ref,
                            *
                            FROM vw_olist_abt_p2;'''
    else:
        query_exec += f'''INSERT INTO TB_ABT
                            SELECT '{safra}' as dt_ref,
                            *
                            FROM vw_olist_abt_p2;'''

    print(safra)
    exec_etl(query_exec, con)

def get_models(DATA_PATH, model_path):
    ''' Function to train all models and choose best one
    '''
    con = sqlalchemy.create_engine( "sqlite:///"+DATA_PATH )
    df = pd.read_sql_table( "TB_ABT", con )

    target = 'fl_venda'
    to_remove = (['seller_id', 'seller_city', 'seller_zip_code_prefix', 
                  'dt_ref'] + [target])
    cat_vars = ['seller_state']
    num_vars = (list(set(df.columns.tolist()) 
                     - set( to_remove ) 
                     - set(cat_vars)))

    tend_var = [i for i in num_vars if i.startswith("tend")]
    qtd_vars = ([i for i in num_vars if i.startswith("qtd") 
                 or i.startswith("quant")])
    media_vars = [i for i in num_vars if i.startswith("media")]
    max_vars = [i for i in num_vars if i.startswith("max")]
    dias = [i for i in num_vars if i.startswith("dias")]
    prop = [i for i in num_vars if i.startswith("prop")]

    df['dt_ref'] = pd.to_datetime(df['dt_ref']) 
    max_dt = df['dt_ref'].max() 
    df_oot = df[df['dt_ref']==max_dt].copy() 
    df_train = df[df['dt_ref'] < max_dt].copy() 

    X_train, X_test, y_train, y_test = (model_selection.train_test_split( 
        df_train[num_vars + cat_vars], df_train[target], 
        random_state=1992, test_size=0.25))

    imputer_zero = mdi.ArbitraryNumberImputer( arbitrary_number=0,
                    variables=qtd_vars+media_vars+max_vars+dias+prop)

    imputer_um = mdi.ArbitraryNumberImputer( arbitrary_number=1,
                                            variables=tend_var)

    onehot = ce.OneHotCategoricalEncoder(variables=cat_vars, 
                                         drop_last=True) 

    # Regressão Logística
    ## Definição do modelo
    model_rl = linear_model.LogisticRegression(penalty='l1', 
                                               solver='liblinear' ) 

    full_pipeline_rl = Pipeline( steps=[('zero', imputer_zero),
                                        ('um', imputer_um),
                                        ("onehot", onehot),
                                        ('model', model_rl) ] )
    ## linspace
    param_grid = { 'model__C':np.linspace(0,0.22,5), 
                'model__random_state':[1992]}

    search_rl = model_selection.GridSearchCV(full_pipeline_rl,
                                            param_grid,
                                            cv=3,
                                            n_jobs=-1,
                                            scoring='roc_auc')
    ## Executa o treinamento!!
    search_rl.fit(X_train, y_train) 

    best_model_rl = search_rl.best_estimator_

    # Árvore de Decisão
    ## Definição do modelo
    model_tree = tree.DecisionTreeClassifier() 

    full_pipeline_tree = Pipeline( steps=[ ('zero', imputer_zero),
                                        ('um', imputer_um),
                                        ("onehot", onehot),
                                        ('modelo', model_tree) ] )

    param_grid = { "modelo__max_depth":[None, 5,6,7],
                "modelo__min_samples_split":[2,5],
                "modelo__min_samples_leaf":[1,2,5] }
    ## Declaração
    search_tree = model_selection.GridSearchCV(full_pipeline_tree,
                                            param_grid,
                                            cv=3,
                                            n_jobs=-1,
                                scoring='neg_root_mean_squared_error') 
    # Executa o treinamento!!
    search_tree.fit(X_train, y_train) 

    best_model_tree = search_tree.best_estimator_

    # Random Forest
    ## Definição do modelo
    model_rf = ensemble.RandomForestClassifier(random_state=1992) 

    full_pipeline_rf = Pipeline( steps=[('zero', imputer_zero),
                                        ('um', imputer_um),
                                        ('onehot', onehot),
                                        ('modelo', model_rf)])

    param_grid = { "modelo__n_estimators":[10,20],
                "modelo__max_depth":[5,10],
                "modelo__min_samples_split":[10,12],
                "modelo__min_samples_leaf":[5,10] }
    ## Declaração
    search_rf = model_selection.GridSearchCV(full_pipeline_rf,
                                            param_grid,
                                            cv=3,
                                            n_jobs=-1,
                                scoring='neg_root_mean_squared_error') 
    ## Executa o treinamento!!
    search_rf.fit(X_train, y_train) 

    best_model_rf = search_rf.best_estimator_

    # XGBoost 1
    model_xgb = xgb.XGBClassifier(random_state=1992)

    full_pipeline_xgb = Pipeline( steps=[('zero', imputer_zero),
                                        ('um', imputer_um),
                                        ('onehot', onehot),
                                        ('modelo', model_xgb)])

    param_grid = { "modelo__n_estimators":[10,50],
                "modelo__max_depth":[5,10],
                "modelo__eta":[0.1, 0.3],
                "modelo__subsample":[0.1, 0.2] }
    ##Declaração
    search_xgb = model_selection.GridSearchCV(full_pipeline_xgb,
                                            param_grid,
                                            cv=3,
                                            n_jobs=-1,
                                scoring='neg_root_mean_squared_error') 
    ##Executa o treinamento!!
    search_xgb.fit(X_train, y_train) 

    best_model_xgb = search_xgb.best_estimator_

    # XGBoost 2
    model_xgb = xgb.XGBClassifier(random_state=1992)

    full_pipeline_xgb_nt = Pipeline( steps=[('onehot', onehot),
                                            ('modelo', model_xgb)])

    param_grid = {"modelo__n_estimators":[10,50],
                "modelo__max_depth":[3,5],
                "modelo__eta":[0.1, 0.3],
                "modelo__subsample":[0.1, 0.2] }
    ## Declaração
    search_xgb_nt = model_selection.GridSearchCV(full_pipeline_xgb_nt,
                                            param_grid,
                                            cv=3,
                                            n_jobs=-1,
                                scoring='neg_root_mean_squared_error') 
    # Executa o treinamento!!
    search_xgb_nt.fit(X_train, y_train) 

    best_model_xgb_nt = search_xgb_nt.best_estimator_

    # Verificando erro na base de teste
    y_test_prob_rl = best_model_rl.predict_proba(X_test)[:, 1]
    y_test_prob_tree = best_model_tree.predict_proba(X_test)[:, 1]
    y_test_prob_rf = best_model_rf.predict_proba(X_test)[:, 1]
    y_test_prob_xgb = best_model_xgb.predict_proba(X_test)[:, 1]
    y_test_prob_xgb_nt = best_model_xgb_nt.predict_proba(X_test)[:, 1]

    auc_test_rl = metrics.roc_auc_score( y_test, y_test_prob_rl)
    auc_test_tree = metrics.roc_auc_score( y_test, y_test_prob_tree)
    auc_test_rf = metrics.roc_auc_score( y_test, y_test_prob_rf)
    auc_test_xgb = metrics.roc_auc_score( y_test, y_test_prob_xgb)
    auc_test_xgb_nt = metrics.roc_auc_score( y_test, y_test_prob_xgb_nt)

    best_model_xgb_nt.fit( df_train[num_vars + cat_vars], 
                           df_train[target])
    
    # Verificando erro na base de Out of Time
    y_test_prob = (best_model_xgb_nt.predict_proba(
                                    df_oot[num_vars + cat_vars ])[:, 1])
    auc_oot = metrics.roc_auc_score( df_oot[target], y_test_prob)
    print( "Área sob a curva ROC:", auc_oot)

    best_model_xgb_nt.fit(df[num_vars+cat_vars], df[target])

    features = (best_model_xgb_nt[:-1].transform( df_train[num_vars + 
                                          cat_vars] ).columns.tolist())

    features_importance = pd.Series(
           best_model_xgb_nt[-1].feature_importances_, index= features)

    features_importance.sort_values( ascending=False ).head(20)

    model_s = pd.Series( {"cat_vars":cat_vars,
                      "num_vars":num_vars,
                      "fit_vars": X_train.columns.tolist(),
                      "model":best_model_xgb_nt,
                      "auc":{"test": auc_test_xgb_nt, "oot":auc_oot}} )

    model_s.to_pickle(model_path + "best_model_olist_xgb_nt.pkl")


if __name__ == '__main__':
    get_abt(DATA_PATH, QUERY_PATH, primeira_safra, ultima_safra)
    get_models(DATA_PATH, model_path)