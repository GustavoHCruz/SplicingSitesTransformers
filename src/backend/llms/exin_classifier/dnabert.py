import random
import time

import torch
from plyer import notification
from torch.optim import AdamW
from torch.utils.data import DataLoader, Dataset, random_split
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from classes.SplicingTransformers import SplicingTransformers

try:
	from IPython import get_ipython
	in_notebook = get_ipython() is not None and 'IPKernelApp' in get_ipython().config
except ImportError:
	in_notebook = False

if in_notebook:
	from tqdm.notebook import tqdm
else:
	from tqdm import tqdm

class ExInSeqsDNABERT(SplicingTransformers):
	class __SpliceDNABERTDataset__(Dataset):
		def __init__(self, data, tokenizer, max_length):
			self.data = data
			self.tokenizer = tokenizer
			self.max_length = max_length

		def __len__(self):
			return len(self.data["sequence"])
		
		def __getitem__(self, idx):
			sequence = self.data["sequence"][idx]

			input_ids = self.tokenizer.encode(sequence, max_length=self.max_length, padding=True, truncation=True)
			label = self.data["label"][idx]

			return torch.tensor(input_ids), torch.tensor(label)
	
	def __init__(self, checkpoint="zhihan1996/DNA_bert_6", device="cuda", seed=None, notification=False,  logs_dir="logs", models_dir="models", alias=None, log_level="info"):
		if seed:
			self._set_seed(seed)
		
		self.log_level = log_level

		if checkpoint != "zhihan1996/DNA_bert_6":
			self.load_checkpoint(checkpoint)
		else:
			self.model = AutoModelForSequenceClassification.from_pretrained(checkpoint, num_labels=2)
			self.tokenizer = AutoTokenizer.from_pretrained(checkpoint)
		
		self.intron_token = 0
		self.exon_token = 1
		super().__init__(checkpoint=checkpoint, device=device, seed=seed, notification=notification, logs_dir=logs_dir, models_dir=models_dir, alias=alias)
		
	def load_checkpoint(self, path):
		self.model = AutoModelForSequenceClassification.from_pretrained(path)
		self.tokenizer = AutoTokenizer.from_pretrained(path)

	def _collate_fn(self, batch):
		input_ids, labels = zip(*batch)
		input_ids_padded = torch.nn.utils.rnn.pad_sequence(input_ids, batch_first=True, padding_value=self.tokenizer.pad_token_id)
		attention_mask = (input_ids_padded != self.tokenizer.pad_token_id).long()
		return input_ids_padded, attention_mask, torch.tensor(labels)
	
	def _process_sequence(self, sequence):
		k = 6

		remain = len(sequence) % k
		if remain != 0:
			padding_len = k - remain
			sequence += 'N' * padding_len

		k_mers = [sequence[i:i+k] for i in range(0, len(sequence), k)]
		return " ".join(k_mers)
	
	def _process_target(self, label):
		return 0 if label == "intron" else 1
	
	def _process_data(self, data):
		data["sequence"] = [self._process_sequence(sequence) for sequence in data["sequence"]]
		data["label"] = [self._process_target(label) for label in data["label"]]

		return data
	
	def add_train_data(self, data, sequence_len=512, batch_size=32, data_config=None):
		if sequence_len > 512:
			raise ValueError("cannot support sequences_len higher than 512")

		self._data_config = {"sequence_len": sequence_len}
		
		data = self._process_data(data)

		dataset = self.__SpliceDNABERTDataset__(data, self.tokenizer, max_length=sequence_len)

		self.train_dataset = dataset
		self.train_dataloader = DataLoader(self.train_dataset, batch_size=batch_size, shuffle=True, collate_fn=self._collate_fn)

	def _check_test_compatibility(self, sequence_len, batch_size):
		if hasattr(self, "_train_config"):
			if self._train_config["sequence_len"] != sequence_len or \
			self._train_config["batch_size"] != batch_size:
				print("Detected a different test dataloader configuration of the one used during training. This may lead to suboptimal results.")

	def add_test_data(self, data, batch_size=32, sequence_len=512, data_config=None):
		self._check_test_compatibility(sequence_len=sequence_len, batch_size=batch_size)

		data = self._process_data(data)

		self.test_dataset = self.__SpliceDNABERTDataset__(data, self.tokenizer, max_length=sequence_len)
		self.test_dataloader = DataLoader(self.test_dataset, batch_size=batch_size, shuffle=True, collate_fn=self._collate_fn)

	def train(self, lr=5e-5, epochs=3, save_at_end=True, save_freq=5):
		if not hasattr(self, "train_dataloader"):
			raise ValueError("Cannot find the train dataloader, make sure you initialized it.")
		
		self.start_time = time.time()
		self._get_next_model_dir()
		
		self.model.to(self._device)
		self.optimizer = AdamW(self.model.parameters(), lr=lr)

		self._train_config = dict(**{
			"lr": lr,
			"epochs": epochs
		}, **self._data_config)
		if hasattr(self, "seed"):
			self._train_config.update({
				"seed": self.seed
			})

		history = {"epoch": [], "time": [], "train_loss": []}

		for epoch in range(epochs):
			self.model.train()
			train_loss = 0

			if self.log_level == "info":
				train_bar = tqdm(self.train_dataloader, desc=f"Training Epoch {epoch+1}/{epochs}", leave=True)
			for batch in self.train_dataloader:
				self.optimizer.zero_grad()

				input_ids, attention_mask, labels = [b.to(self._device) for b in batch]
				outputs = self.model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)

				loss = outputs.loss
				loss.backward()
				self.optimizer.step()
				train_loss += loss.item()

				if self.log_level == "info":
					train_bar.update(1)
					train_bar.set_postfix({"Loss": train_loss/train_bar.n})

			train_loss /= len(self.train_dataloader)
			if self.log_level == "info":
				train_bar.set_postfix({"Loss": train_loss})
				train_bar.close()
			history["train_loss"].append(train_loss)

			history["epoch"].append(epoch)

			if save_freq and (epoch+1) % save_freq == 0:
				self._save_checkpoint(epoch=epoch)

			self.epoch_end_time = time.time()
			history["time"].append(self.epoch_end_time - self.start_time)

		if self.notification:
			notification.notify(title="Training complete", timeout=5)

		torch.cuda.empty_cache()

		self._save_history(history=history)
		self._save_config()

		if save_at_end:
			self.save_checkpoint()

	def evaluate(self):
		if not hasattr(self, "test_dataloader"):
			raise ValueError("Can't find the test dataloader, make sure you initialized it.")
		
		if not hasattr(self, "_logs_dir"):
			self._get_next_model_dir()

		self.model.to(self._device)
		total_loss = 0
		total_correct = 0
		total_samples = 0
		exon_correct = 0
		exon_total = 0
		intron_correct = 0
		intron_total = 0

		self.model.eval()
		with torch.no_grad():
			if self.log_level == "info":
				eval_bar = tqdm(self.test_dataloader, desc="Evaluating", leave=True)
			for batch in self.test_dataloader:
				input_ids, attention_mask, labels = [b.to(self._device) for b in batch]
				outputs = self.model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
				
				loss = outputs.loss
				total_loss += loss.item()

				predictions = torch.argmax(outputs.logits, dim=-1)

				for prediction, label in zip(predictions, labels):
					if prediction == label:
						total_correct += 1
					
						if label.item() == self.exon_token:
							exon_correct += 1
						else:
							intron_correct += 1

					if label.item() == self.exon_token:
						exon_total += 1
					else:
						intron_total += 1
					
					total_samples += 1

				if self.log_level == "info":
					eval_bar.update(1)
					eval_bar.set_postfix({"Eval loss": total_loss/eval_bar.n})
		
		if self.log_level == "info":
			eval_bar.close()
		total_loss /= len(self.test_dataloader)
		overall_accuracy = total_correct / total_samples if total_samples > 0 else 0.0
		exon_accuracy = exon_correct / exon_total if exon_total > 0 else 0.0
		intron_accuracy = intron_correct / intron_total if intron_total > 0 else 0.0

		print(f"Evaluation complete. Average loss: {total_loss:.4f}")
		print(f"Overall Accuracy: {overall_accuracy:.4f}")
		print(f"Exon accuracy: {exon_accuracy:.4f}")
		print(f"Intron accuracy: {intron_accuracy:.4f}")

		self._eval_results = {
			"avg loss": total_loss,
			"overall accuracy": overall_accuracy,
			"exon accuracy": exon_accuracy,
			"intron accuracy": intron_accuracy
		}

		self._save_evaluation_results()

		if self.notification:
			notification.notify(title="Evaluation complete", timeout=5)
	
	def _prediction_mapping(self, prediction):
		return "intron" if torch.argmax(prediction.logits, dim=-1).tolist() == [0] else "exon"
	
	def predict_single(self, data, map_pred=True):
		sequence = self._process_sequence(data["sequence"])
		input_ids = self.tokenizer.encode(sequence, return_tensors="pt").to(self._device)

		self.model.eval()
		with torch.no_grad():
			prediction = self.model(input_ids=input_ids)

		if map_pred:
			return self._prediction_mapping(prediction=prediction)

		return prediction
