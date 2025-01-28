import random

import torch
from plyer import notification
from torch.optim import AdamW
from torch.utils.data import DataLoader, Dataset, random_split
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          BertForSequenceClassification, BertTokenizer)

from SplicingTransformers import SplicingTransformers

try:
	from IPython import get_ipython
	in_notebook = get_ipython() is not None and 'IPKernelApp' in get_ipython().config
except ImportError:
	in_notebook = False

if in_notebook:
	from tqdm.notebook import tqdm
else:
	from tqdm import tqdm

class SpliceBERT(SplicingTransformers):
	class __SpliceBERTDataset__(Dataset):
		def __init__(self, data, tokenizer, sequence_len, flanks_len, feat_hide_prob):
			self.data = data
			self.tokenizer = tokenizer
			self.max_length = sequence_len + flanks_len * 2 + 100
			self.feat_hide_prob = feat_hide_prob

		def __len__(self):
			return len(self.data["sequence"])
		
		def __getitem__(self, idx):
			prompt = f"Sequence:{self.data['sequence'][idx]}[SEP]"

			if len(self.data["organism"]) > idx and self.data["organism"][idx]:
				if random.random() > self.feat_hide_prob:
					prompt += f"Organism:{self.data["organism"][idx][:20]}[SEP]"
			
			if len(self.data["gene"]) > idx and self.data["gene"][idx]:
				if random.random() > self.feat_hide_prob:
					prompt += f"Gene:{self.data["gene"][idx][:20]}[SEP]"

			if len(self.data["flank_before"]) > idx and self.data["flank_before"][idx]:
				if random.random() > self.feat_hide_prob:
					prompt += f"Flank Before:{self.data["flank_before"][idx]}[SEP]"

			if len(self.data["flank_after"]) > idx and self.data["flank_after"][idx]:
				if random.random() > self.feat_hide_prob:
					prompt += f"Flank After:{self.data["flank_after"][idx]}[SEP]"
			
			prompt += "Answer:"

			input_ids = self.tokenizer.encode(prompt, max_length=self.max_length, padding=True, truncation=True)
			label = self.data["label"][idx]

			return torch.tensor(input_ids), torch.tensor(label)
	
	def __init__(self, checkpoint="bert-base-uncased", device="cuda", seed=None, notification=False,  logs_dir="logs", alias=None):
		if checkpoint != "bert-base-uncased":
			self.load_checkpoint(checkpoint)
		else:
			self.model = BertForSequenceClassification.from_pretrained(checkpoint, num_labels=2)
			self.tokenizer = BertTokenizer.from_pretrained(checkpoint, do_lower_case=False)
		
		if checkpoint == "bert-base-uncased":
			special_tokens = ["[A]", "[C]", "[G]", "[T]", "[R]", "[Y]", "[S]", "[W]", "[K]", "[M]", "[B]", "[D]", "[H]", "[V]", "[N]"]
			self.tokenizer.add_tokens(special_tokens)
			self.model.resize_token_embeddings(len(self.tokenizer), mean_resizing=False)

		self.intron_token = 0
		self.exon_token = 1
		super().__init__(checkpoint=checkpoint, device=device, seed=seed, notification=notification, logs_dir=logs_dir, alias=alias)
		
	def load_checkpoint(self, path):
		self.model = BertForSequenceClassification.from_pretrained(path)
		self.tokenizer = BertTokenizer.from_pretrained(path)

	def _collate_fn(self, batch):
		input_ids, labels = zip(*batch)
		input_ids_padded = torch.nn.utils.rnn.pad_sequence(input_ids, batch_first=True, padding_value=self.tokenizer.pad_token_id)
		attention_mask = (input_ids_padded != self.tokenizer.pad_token_id).long()
		return input_ids_padded, attention_mask, torch.tensor(labels)
	
	def _process_sequence(self, sequence):
		return f"".join(f"[{nucl.upper()}]" for nucl in sequence)
	
	def _process_label(self, label):
		return 0 if label == "intron" else 1
	
	def _process_data(self, data):
		data["sequence"] = [self._process_sequence(sequence) for sequence in data["sequence"]]
		data["label"] = [self._process_label(label) for label in data["label"]]
		data["flank_before"] = [self._process_sequence(sequence) for sequence in data["flank_before"]]
		data["flank_after"] = [self._process_sequence(sequence) for sequence in data["flank_after"]]

		return data
	
	def add_train_data(self, data, sequence_len=512, flanks_len=10, batch_size=32, train_percentage=0.8, feat_hide_prob=0.01):
		if sequence_len > 512:
			raise ValueError("cannot support sequences_len higher than 512")
		if flanks_len > 50:
			raise ValueError("cannot support flanks_len higher than 50")

		self._data_config = {
			"sequence_len": sequence_len,
			"flanks_len": flanks_len,
			"batch_size": batch_size,
			"feat_hide_prob": feat_hide_prob
		}
		
		data = self._process_data(data)

		dataset = self.__SpliceBERTDataset__(data, self.tokenizer, sequence_len=sequence_len, flanks_len=flanks_len, feat_hide_prob=feat_hide_prob)

		if train_percentage == 1.0:
			self.train_dataset = dataset
		else:
			total_size = len(dataset)
			train_size = int(total_size * train_percentage)
			eval_size = total_size - train_size
			self.train_dataset, self.eval_dataset = random_split(dataset, [train_size, eval_size])
			self.eval_dataloader = DataLoader(self.eval_dataset, batch_size=batch_size, shuffle=True, collate_fn=self._collate_fn)
		
		self.train_dataloader = DataLoader(self.train_dataset, batch_size=batch_size, shuffle=True, collate_fn=self._collate_fn)

	def _check_test_compatibility(self, sequence_len, flanks_len, batch_size, feat_hide_prob):
		if hasattr(self, "_train_config"):
			if self._train_config["sequence_len"] != sequence_len or \
			self._train_config["flanks_len"] != flanks_len or \
			self._train_config["batch_size"] != batch_size or \
			self._train_config["feat_hide_prob"] != feat_hide_prob:
				print("Detected a different test dataloader configuration of the one used during training. This may lead to suboptimal results.")

	def add_test_data(self, data, sequence_len=512, flanks_len=10, batch_size=32, feat_hide_prob=0.01):
		self._check_test_compatibility(sequence_len=sequence_len, flanks_len=flanks_len, batch_size=batch_size, feat_hide_prob=feat_hide_prob)

		data = self._process_data(data)

		self.test_dataset = self.__SpliceBERTDataset__(data, self.tokenizer, sequence_len=sequence_len, flanks_len=flanks_len, feat_hide_prob=feat_hide_prob)
		self.test_dataloader = DataLoader(self.test_dataset, batch_size=batch_size, shuffle=True, collate_fn=self._collate_fn)

	def train(self, lr=2e-5, epochs=3, save_at_end=None, evaluation=True, keep_best=False, save_freq=5):
		if not hasattr(self, "train_dataloader"):
			raise ValueError("Cannot find the train dataloader, make sure you initialized it.")
		
		self._get_next_model_dir()
		
		self.model.to(self._device)
		self.optimizer = AdamW(self.model.parameters(), lr=lr)

		self._train_config = dict(**{
			"lr": lr,
			"epochs": epochs,
			"seed": self.seed
		}, **self._data_config)

		history = {"epoch": [], "train_loss": []}
		if evaluation:
			history.update({"eval_loss": [], "eval_accuracy": []})

		best_eval_loss = float("inf")

		for epoch in range(epochs):
			self.model.train()
			train_loss = 0

			train_bar = tqdm(self.train_dataloader, desc=f"Training Epoch {epoch+1}/{epochs}", leave=True)
			for batch in self.train_dataloader:
				self.optimizer.zero_grad()

				input_ids, attention_mask, labels = [b.to(self._device) for b in batch]
				outputs = self.model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)

				loss = outputs.loss
				loss.backward()
				self.optimizer.step()
				train_loss += loss.item()

				train_bar.update(1)
				train_bar.set_postfix({"Loss": train_loss/train_bar.n})

			train_loss /= len(self.train_dataloader)
			history["train_loss"].append(train_loss)
			train_bar.set_postfix({"Loss": train_loss})
			train_bar.close()

			if evaluation:
				best = False
				self.model.eval()
				eval_loss = 0
				correct_predictions = 0
				total_predictions = 0

				eval_bar = tqdm(self.eval_dataloader, desc="Validating", leave=True)
				with torch.no_grad():
					for batch in self.eval_dataloader:
						input_ids, attention_mask, labels = [b.to(self._device) for b in batch]
						outputs = self.model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)

						loss = outputs.loss
						eval_loss += loss.item()

						predictions = torch.argmax(outputs.logits, dim=-1)
						correct_predictions += (predictions == labels).sum().item()
						total_predictions += labels.size(0)

						eval_bar.update(1)
						eval_bar.set_postfix({"Eval loss": eval_loss/eval_bar.n})

				eval_loss /= len(self.eval_dataloader)
				eval_accuracy = correct_predictions / total_predictions
				eval_bar.set_postfix({"Eval loss": eval_loss, "Eval Accuracy": eval_accuracy})
				eval_bar.close()
				history["eval_loss"].append(eval_loss)
				history["eval_accuracy"].append(eval_accuracy)

			history["epoch"].append(epoch)

			if (epoch+1) % save_freq == 0:
				self._save_checkpoint(epoch=epoch)

			if eval_loss < best_eval_loss:
				best = True
				best_eval_loss = eval_loss
				self._save_checkpoint()

		if keep_best:
			if evaluation:
				if not best:
					self._load_checkpoint()
			
			else:
				print("Cannot load best because evaluation is setted off. To be able to restore the best model from the stored ones, please allow evaluation to execute with trainning. ")

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

				eval_bar.update(1)
				eval_bar.set_postfix({"Eval loss": total_loss/eval_bar.n})
			
		eval_bar.close()
		total_loss /= len(self.test_dataloader)
		overall_accuracy = total_correct / total_samples if total_samples > 0 else 0.0
		exon_accuracy = exon_correct / exon_total if exon_total > 0 else 0.0
		intron_accuracy = intron_correct / intron_total if intron_total > 0 else 0.0

		print(f"Evaluation complete")
		print(f"Average loss: {total_loss:.4f}")
		print(f"Overall Accuracy: {overall_accuracy:.4f}")
		print(f"Exon accuracy: {exon_accuracy:.4f}")
		print(f"Intron accuracy: {intron_accuracy:.4f}")

		if self.notification:
			notification.notify(title="Evaluation complete", timeout=5)
	
	def _prediction_mapping(self, prediction):
		return "intron" if torch.argmax(prediction.logits, dim=-1).tolist() == [0] else "exon"
	
	def predict_single(self, data, map_pred=True):
		sequence = self._process_sequence(data["sequence"])
		
		keys = ["gene", "organism", "flank_before", "flank_after"]
		prompt = f"Sequence: {sequence}\n"
		for key in keys:
			if hasattr(data, key):
				prompt += f"{key.capitalize()}: {data[key]}\n"
		prompt += "Answer: "

		input_ids = self.tokenizer.encode(prompt, return_tensors="pt").to(self._device)

		self.model.eval()
		with torch.no_grad():
			prediction = self.model(input_ids=input_ids)

		if map_pred:
			return self._prediction_mapping(prediction)
		
		return prediction
	
class SpliceDNABERT(SplicingTransformers):
	class __SpliceDNABERTDataset__(Dataset):
		def __init__(self, data, tokenizer, max_length):
			self.data = data
			self.tokenizer = tokenizer
			self.max_length = max_length

		def __len__(self):
			return len(self.data["sequence"])
		
		def __getitem__(self, idx):
			sequence = self.data['sequence'][idx]

			input_ids = self.tokenizer.encode(sequence, max_length=self.max_length, padding=True, truncation=True)
			label = self.data["label"][idx]

			return torch.tensor(input_ids), torch.tensor(label)
	
	def __init__(self, checkpoint="zhihan1996/DNA_bert_6", device="cuda", seed=None, notification=False,  logs_dir="logs", alias=None):
		if checkpoint != "zhihan1996/DNA_bert_6":
			self.load_checkpoint(checkpoint)
		else:
			self.model = AutoModelForSequenceClassification.from_pretrained(checkpoint, num_labels=2)
			self.tokenizer = AutoTokenizer.from_pretrained(checkpoint)
		
		self.intron_token = 0
		self.exon_token = 1
		super().__init__(checkpoint=checkpoint, device=device, seed=seed, notification=notification, logs_dir=logs_dir, alias=alias)
		
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
	
	def _process_label(self, label):
		return 0 if label == "intron" else 1
	
	def _process_data(self, data):
		data["sequence"] = [self._process_sequence(sequence) for sequence in data["sequence"]]
		data["label"] = [self._process_label(label) for label in data["label"]]

		return data
	
	def add_train_data(self, data, sequence_len=512, batch_size=32, train_percentage=0.8):
		if sequence_len > 512:
			raise ValueError("cannot support sequences_len higher than 512")

		self._data_config = {"sequence_len": sequence_len}
		
		data = self._process_data(data)

		dataset = self.__SpliceDNABERTDataset__(data, self.tokenizer, max_length=sequence_len)

		if train_percentage == 1.0:
			self.train_dataset = dataset
		else:
			total_size = len(dataset)
			train_size = int(total_size * train_percentage)
			eval_size = total_size - train_size
			self.train_dataset, self.eval_dataset = random_split(dataset, [train_size, eval_size])
			self.eval_dataloader = DataLoader(self.eval_dataset, batch_size=batch_size, shuffle=True, collate_fn=self._collate_fn)
		
		self.train_dataloader = DataLoader(self.train_dataset, batch_size=batch_size, shuffle=True, collate_fn=self._collate_fn)

	def _check_test_compatibility(self, sequence_len, batch_size):
		if hasattr(self, "_train_config"):
			if self._train_config["sequence_len"] != sequence_len or \
			self._train_config["batch_size"] != batch_size:
				print("Detected a different test dataloader configuration of the one used during training. This may lead to suboptimal results.")

	def add_test_data(self, data, sequence_len=512, batch_size=32):
		self._check_test_compatibility(sequence_len=sequence_len, batch_size=batch_size)

		data = self._process_data(data)

		self.test_dataset = self.__SpliceDNABERTDataset__(data, self.tokenizer, max_length=sequence_len)
		self.test_dataloader = DataLoader(self.test_dataset, batch_size=batch_size, shuffle=True, collate_fn=self._collate_fn)

	def train(self, lr=5e-5, epochs=3, save_at_end=None, evaluation=True, keep_best=False, save_freq=5):
		if not hasattr(self, "train_dataloader"):
			raise ValueError("Cannot find the train dataloader, make sure you initialized it.")
		
		self._get_next_model_dir()
		
		self.model.to(self._device)
		self.optimizer = AdamW(self.model.parameters(), lr=lr)

		self._train_config = dict(**{
			"lr": lr,
			"epochs": epochs,
			"seed": self.seed
		}, **self._data_config)

		history = {"epoch": [], "train_loss": [], "eval_loss": [], "eval_accuracy": []}

		best_eval_loss = float("inf")

		for epoch in range(epochs):
			self.model.train()
			train_loss = 0

			train_bar = tqdm(self.train_dataloader, desc=f"Training Epoch {epoch+1}/{epochs}", leave=True)
			for batch in self.train_dataloader:
				self.optimizer.zero_grad()

				input_ids, attention_mask, labels = [b.to(self._device) for b in batch]
				outputs = self.model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)

				loss = outputs.loss
				loss.backward()
				self.optimizer.step()
				train_loss += loss.item()

				train_bar.update(1)
				train_bar.set_postfix({"Loss": train_loss/train_bar.n})

			train_loss /= len(self.train_dataloader)
			history["train_loss"].append(train_loss)
			train_bar.set_postfix({"Loss": train_loss})
			train_bar.close()

			if evaluation:
				best = False
				self.model.eval()
				eval_loss = 0
				correct_predictions = 0
				total_predictions = 0

				eval_bar = tqdm(self.eval_dataloader, desc="Validating", leave=True)
				with torch.no_grad():
					for batch in self.eval_dataloader:
						input_ids, attention_mask, labels = [b.to(self._device) for b in batch]
						outputs = self.model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)

						loss = outputs.loss
						eval_loss += loss.item()

						predictions = torch.argmax(outputs.logits, dim=-1)
						correct_predictions += (predictions == labels).sum().item()
						total_predictions += labels.size(0)

						eval_bar.update(1)
						eval_bar.set_postfix({"Eval loss": eval_loss/eval_bar.n})

				eval_loss /= len(self.eval_dataloader)
				eval_accuracy = correct_predictions / total_predictions
				eval_bar.set_postfix({"Eval loss": eval_loss, "Eval Accuracy": eval_accuracy})
				eval_bar.close()
				history["eval_loss"].append(eval_loss)
				history["eval_accuracy"].append(eval_accuracy)

			history["epoch"].append(epoch)

			if (epoch+1) % save_freq == 0:
				self._save_checkpoint(epoch=epoch)

			if eval_loss < best_eval_loss:
				best = True
				best_eval_loss = eval_loss
				self._save_checkpoint()

		if keep_best:
			if evaluation:
				if not best:
					self._load_checkpoint()
			
			else:
				print("Cannot load best because evaluation is setted off. To be able to restore the best model from the stored ones, please allow evaluation to execute with trainning. ")

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

				eval_bar.update(1)
				eval_bar.set_postfix({"Eval loss": total_loss/eval_bar.n})
			
		eval_bar.close()
		total_loss /= len(self.test_dataloader)
		overall_accuracy = total_correct / total_samples if total_samples > 0 else 0.0
		exon_accuracy = exon_correct / exon_total if exon_total > 0 else 0.0
		intron_accuracy = intron_correct / intron_total if intron_total > 0 else 0.0

		print(f"Evaluation complete. Average loss: {total_loss:.4f}")
		print(f"Overall Accuracy: {overall_accuracy:.4f}")
		print(f"Exon accuracy: {exon_accuracy:.4f}")
		print(f"Intron accuracy: {intron_accuracy:.4f}")

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
