import pandas as pd

from classes.RebuildSeqs_GPT import RebuildSeqsGPT
from funcs.config_reading import read_datasets_configs, read_experiment_configs

config = read_experiment_configs("RebuildSeqs")
datasets_config = read_datasets_configs("RebuildSeqs")

dataset_names = [i["name"] for i in datasets_config["sizes"]]

train_dataset = config["train_dataset"]
test_dataset = config["test_dataset"]

if config["checkpoint_base"] not in config["supported_models"]["all"]:
  raise ValueError("Default Checkpoint Not Found")
if train_dataset == None and test_dataset == None:
  raise ValueError("Both datasets defined as 'None'")
if train_dataset not in dataset_names and train_dataset != None:
  raise ValueError("Train Dataset Not Found")
if test_dataset not in dataset_names and test_dataset != None:
  raise ValueError("Test Dataset Not Found")
if config["dataset_version"] not in ["small", "normal"]:
  raise ValueError("Dataset Version Should be Small or Normal")

train_df_path = f"datasets/{config["train_dataset"]}"
test_df_path = f"datasets/{config["test_dataset"]}"
if config["dataset_version"] == "small":
  train_df_path += "_small.csv"
  test_df_path += "_small.csv"
else:
  train_df_path += ".csv"
  test_df_path += ".csv"

if train_dataset:
  train_df = pd.read_csv(train_df_path, keep_default_na=False)

  train_sequence = train_df.iloc[:, 0].tolist()
  train_builded = train_df.iloc[:, 1].tolist()
  train_organism = train_df.iloc[:, 2].tolist()

if test_dataset:
  test_df = pd.read_csv(test_df_path, keep_default_na=False)
  
  test_sequence = test_df.iloc[:, 0].tolist()
  test_builded = test_df.iloc[:, 1].tolist()
  test_organism = test_df.iloc[:, 2].tolist()

sequence_len = datasets_config["version"]["normal"]["sequence_len"]
if config["dataset_version"] == "small":
  sequence_len = datasets_config["version"]["small"]["sequence_len"]

model_to_use = config["checkpoint_base"]
if not config["checkpoint_default"]:
  model_to_use = config["checkpoint_to_load"]

model = RebuildSeqsGPT(model_to_use, seed=config["seed"], alias=config["model_name"], log_level=config["log_level"])

model.add_train_data({
  "sequence": train_sequence,
  "builded": train_builded,
  "organism": train_organism,
}, batch_size=config["batch_size"], sequence_len=sequence_len)

model.add_test_data({
  "sequence": test_sequence,
  "builded": test_builded,
  "organism": test_organism,
}, batch_size=config["batch_size"], sequence_len=sequence_len)

model.train(lr=config["lr"], epochs=config["epochs"], save_at_end=True, save_freq=0)
model.evaluate()
