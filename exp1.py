import os
import json
import random
import numpy as np
from pathlib import Path
from typing import Dict
from copy import deepcopy
from tqdm import tqdm
from nltk import word_tokenize

import torch
import torchtext
import torch.nn.functional as F

from transformers import AutoModelForSequenceClassification, AutoTokenizer, EvalPrediction, GlueDataset
from transformers import GlueDataTrainingArguments as DataTrainingArguments
from transformers.data.processors.glue import Sst2Processor
from transformers import (
    HfArgumentParser,
    Trainer,
    TrainingArguments,
    glue_compute_metrics,
    glue_output_modes,
    set_seed,
)

from run_glue import ModelArguments, ExperimentArguments


random.seed(42)
set_seed(42)


def sst2_without_subtrees():
    '''
    The GLUE version of SST-2 contains subtrees.
    We construct a SST-2 wo subtrees in the GLUE format.
    '''
    train, dev, test = torchtext.datasets.SST.splits(
        torchtext.data.Field(batch_first=True, tokenize=word_tokenize, lower=False),
        torchtext.data.Field(sequential=False, unk_token=None),
        root='data/SST-2/',
        train_subtrees=False,  # False by default
    )

    print('train', len(train))
    print('dev', len(dev))
    print('test', len(test))

    data_dir = 'data/SST-2/base_10k'
    output_dir = 'output/SST-2/base_10'
    config_dir = 'configs/SST-2/base_10k.json'
    train_data_dir = os.path.join(data_dir, 'train.tsv')

    Path(data_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(config_dir).parent.mkdir(parents=True, exist_ok=True)

    args = {
        "model_type": "bert",
        "model_name_or_path": "distilbert-base-cased",
        "task_name": "SST-2",
        "do_train": True,
        "do_eval": True,
        "data_dir": "data/SST-2/base_10k",
        "max_seq_length": 128,
        "per_gpu_train_batch_size": 32,
        "learning_rate": 2e-05,
        "num_train_epochs": 3.0,
        "output_dir": "output/SST-2/base_10k",
        "train_data_dir": "data/SST-2/base_10k",
        "eval_data_dir": "data/SST-2/base_10k"
    }
    with open(config_dir, 'w') as f:
        json.dump(args, f, indent=4)

    with open(train_data_dir, 'w') as f:
        f.write('sentence\tlabel\n')
        for example in train:
            f.write('{}\t{}\n'.format(
                ' '.join(example.text),
                '0' if example.label == 'negative' else '1'
            ))


def setup(
    args_dir: str,
    train_data_dir: str = None,
    eval_data_dir: str = None,
):
    parser = HfArgumentParser((ModelArguments, DataTrainingArguments, TrainingArguments,
                               ExperimentArguments))

    model_args, data_args, training_args, experiment_args = parser.parse_json_file(
        json_file=os.path.abspath(args_dir))
    if train_data_dir is not None:
        experiment_args.train_data_dir = train_data_dir
    if eval_data_dir is not None:
        experiment_args.eval_data_dir = eval_data_dir

    output_mode = glue_output_modes[data_args.task_name]

    tokenizer = AutoTokenizer.from_pretrained(training_args.output_dir)
    model = AutoModelForSequenceClassification.from_pretrained(training_args.output_dir)
    model = model.to(training_args.device)

    train_data_args = deepcopy(data_args)
    train_data_args.data_dir = experiment_args.train_data_dir

    eval_data_args = deepcopy(data_args)
    eval_data_args.data_dir = experiment_args.eval_data_dir

    train_dataset = GlueDataset(train_data_args, tokenizer=tokenizer,
                                local_rank=training_args.local_rank)
    eval_dataset = GlueDataset(eval_data_args, tokenizer=tokenizer,
                               local_rank=training_args.local_rank, evaluate=True)

    def compute_metrics(p: EvalPrediction) -> Dict:
        if output_mode == "classification":
            preds = np.argmax(p.predictions, axis=1)
        elif output_mode == "regression":
            preds = np.squeeze(p.predictions)
        return glue_compute_metrics(data_args.task_name, preds, p.label_ids)

    # Initialize our Trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        compute_metrics=compute_metrics,
    )

    return model, trainer, train_dataset, eval_dataset


def create_data_config(
        task_name: str,
        config_name: str,
        train_examples: list = None,
        version_number: int = None,
):
    args = json.load(open('configs/{}/base.json'.format(task_name)))

    if version_number is not None:
        config_name += '/' + str(version_number)
    data_dir = 'data/{}/{}'.format(task_name, config_name)
    output_dir = 'output/{}/{}'.format(task_name, config_name)
    config_dir = 'configs/{}/{}.json'.format(task_name, config_name)
    train_data_dir = os.path.join(data_dir, 'train.tsv')

    Path(data_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(config_dir).parent.mkdir(parents=True, exist_ok=True)

    print(config_dir)

    args.update({
        'train_data_dir': data_dir,
        'output_dir': output_dir,
    })
    with open(config_dir, 'w') as f:
        json.dump(args, f, indent=4)

    if task_name == 'SST-2':
        with open(train_data_dir, 'w') as f:
            f.write('sentence\tlabel\n')
            for example in train_examples:
                f.write('{}\t{}\n'.format(example.text_a, example.label))

    if task_name == 'SNLI':
        with open(train_data_dir, 'w') as f:
            f.write(('Index\t' + 'NULL\t' * 6
                     + 'sentence1\tsentence2\t' + 'NULL\t' * 5
                     + 'gold_label\n'))
            for i, example in enumerate(train_examples):
                f.write(('{}\t' + 'NULL\t' * 6 + '{}\t{}\t' + 'NULL\t' * 5 + '{}\n').format(
                    i, example.text_a, example.text_b, example.label))


def random_dev_set():
    sst_processor = Sst2Processor()
    dev_examples = sst_processor.get_dev_examples('glue_data/SST-2')

    datasets = {
        'combined': dev_examples,
        'negative': [x for x in dev_examples if x.label == '0'],
        'positive': [x for x in dev_examples if x.label == '1'],
    }

    path = 'data/SST-2/base/'
    Path(path).mkdir(parents=True, exist_ok=True)
    output_file = open(path + 'dev.tsv', 'w')
    output_file.write('sentence\tlabel\n')
    n_examples = 50
    for fold, examples in datasets.items():
        random.shuffle(examples)
        for example in examples[:n_examples]:
            output_file.write('{}\t{}\n'.format(example.text_a, example.label))
    output_file.close()


def remove_by_random():
    all_examples = Sst2Processor().get_train_examples('data/SST-2/base')
    negative_examples = [x for x in all_examples if x.label == '0']
    positive_examples = [x for x in all_examples if x.label == '1']
    n_examples_removed = int(0.1 * len(all_examples))
    n_trials = 3

    for i in range(n_trials):
        random.shuffle(all_examples)
        create_data_config(
            'SST-2',
            config_name='random_10_percent_removed_combined',
            train_examples=all_examples[n_examples_removed:],
            version_number=i,
        )

    for i in range(n_trials):
        random.shuffle(positive_examples)
        create_data_config(
            'SST-2',
            config_name='random_10_percent_removed_positive',
            train_examples=positive_examples[n_examples_removed:] + negative_examples,
            version_number=i,
        )

    for i in range(n_trials):
        random.shuffle(negative_examples)
        create_data_config(
            'SST-2',
            config_name='random_10_percent_removed_negative',
            train_examples=negative_examples[n_examples_removed:] + positive_examples,
            version_number=i,
        )


def remove_by_confidence():
    model, trainer, train_dataset, eval_dataset = setup(args_dir='configs/SST-2/base.json')

    output = trainer.predict(train_dataset)
    scores = F.softmax(torch.from_numpy(output.predictions), dim=-1).numpy()
    scores = np.choose(output.label_ids, scores.T)
    indices = np.arange(len(scores))
    positive_indices = indices[output.label_ids == 1]
    negative_indices = indices[output.label_ids == 0]
    positive_scores = scores[output.label_ids == 1]
    negative_scores = scores[output.label_ids == 0]

    most_confident_combined_indices = np.argsort(-scores)
    most_confident_positive_indices = positive_indices[np.argsort(-positive_scores)]
    most_confident_negative_indices = negative_indices[np.argsort(-negative_scores)]

    train_examples = Sst2Processor().get_train_examples('data/SST-2/base')
    n_removed = int(0.1 * len(train_examples))

    datasets = {
        'most_confident_combined_removed': (
            [train_examples[i] for i in most_confident_combined_indices[n_removed:]]
        ),
        'most_confident_positive_removed': (
            [train_examples[i] for i in most_confident_positive_indices[n_removed:]]
            + [train_examples[i] for i in negative_indices]
        ),
        'most_confident_negative_removed': (
            [train_examples[i] for i in most_confident_negative_indices[n_removed:]]
            + [train_examples[i] for i in positive_indices]
        ),
        'least_confident_combined_removed': (
            [train_examples[i] for i in most_confident_combined_indices[::-1][n_removed:]]
        ),
        'least_confident_positive_removed': (
            [train_examples[i] for i in most_confident_positive_indices[::-1][n_removed:]]
            + [train_examples[i] for i in negative_indices]
        ),
        'least_confident_negative_removed': (
            [train_examples[i] for i in most_confident_negative_indices[::-1][n_removed:]]
            + [train_examples[i] for i in positive_indices]
        ),
    }

    for config_name, train_examples in datasets.items():
        create_data_config(
            'SST-2',
            config_name=config_name,
            train_examples=train_examples,
        )


def remove_by_similarity():
    """
    for each example in the target test set, find the training examples with the most similar final
    representation, accumulate the score over all test examples, remove the top 10%
    """
    model, trainer, train_dataset, eval_dataset = setup(args_dir='configs/SST-2/base.json')
    model.eval()

    # use eval dataloader to avoid shuffling
    dataloaders = {
        'eval': trainer.get_eval_dataloader(eval_dataset),
        'train': trainer.get_eval_dataloader(train_dataset),
    }
    pooled_outputs = {'train': [], 'eval': []}
    for fold, dataloader in dataloaders.items():
        for inputs in tqdm(dataloader):
            for k, v in inputs.items():
                inputs[k] = v.to(model.device)
            with torch.no_grad():
                outputs = model.bert(
                    input_ids=inputs['input_ids'],
                    attention_mask=inputs['attention_mask'],
                    token_type_ids=inputs['token_type_ids'],
                )
                pooled_output = outputs[1]
                pooled_outputs[fold].append(pooled_output.detach().cpu().numpy())
    pooled_outputs = {k: np.concatenate(v, axis=0) for k, v in pooled_outputs.items()}
    similarity = pooled_outputs['eval'] @ pooled_outputs['train'].T

    train_examples = Sst2Processor().get_train_examples('data/SST-2/base')
    n_removed = int(0.1 * len(train_examples))

    eval_subset_indices = {
        'combined': list(range(len(eval_dataset))),
        'negative': [i for i, x in enumerate(eval_dataset) if x.label == 0],
        'positive': [i for i, x in enumerate(eval_dataset) if x.label == 1],
    }

    for fold, indices in eval_subset_indices.items():
        most_similar_indices = np.argsort(-similarity[indices].mean(axis=0))

        create_data_config(
            'SST-2',
            config_name='most_similar_to_{}_dev_removed'.format(fold),
            train_examples=[train_examples[i] for i in most_similar_indices[n_removed:]],
        )

        create_data_config(
            'SST-2',
            config_name='least_similar_to_{}_dev_removed'.format(fold),
            train_examples=[train_examples[i] for i in most_similar_indices[::-1][n_removed:]],
        )


def compare_scores(args_dirs: str):
    model, trainer, train_dataset, eval_dataset = setup(args_dir='configs/SST-2/base.json')
    output_original = trainer.predict(eval_dataset)
    scores_original = np.choose(
        output_original.label_ids,
        F.softmax(torch.from_numpy(output_original.predictions), dim=-1).numpy().T,
    )
    predictions_original = np.argmax(output_original.predictions, axis=1)
    print('original predictions: {} positive {} negative'.format(
        sum(predictions_original),
        len(predictions_original) - sum(predictions_original)
    ))

    print('original labels: {} positive {} negative'.format(
        sum(output_original.label_ids),
        len(output_original.label_ids) - sum(output_original.label_ids)
    ))

    for args_dir in args_dirs:
        print(args_dir)
        model, trainer, _, _ = setup(args_dir=args_dir)
        output_modified = trainer.predict(eval_dataset)
        predictions_modified = np.argmax(output_modified.predictions, axis=1)
        predictions = {
            'combined': predictions_modified,
            'negative': predictions_modified[output_modified.label_ids == 0],
            'positive': predictions_modified[output_modified.label_ids == 1],
        }

        scores_modified = np.choose(
            output_modified.label_ids,
            F.softmax(torch.from_numpy(output_modified.predictions), dim=-1).numpy().T,
        )
        scores_diff = {
            'combined': scores_modified - scores_original,
            'negative': (scores_modified - scores_original)[output_modified.label_ids == 0],
            'positive': (scores_modified - scores_original)[output_modified.label_ids == 1],
        }

        for fold, diff in scores_diff.items():
            print('{} predictions: {} positive {} negative'.format(
                fold,
                sum(predictions[fold]),
                len(predictions[fold]) - sum(predictions[fold])
            ))
            print('{}: {}{:.4f}%'.format(fold, '+' if np.mean(diff) > 0 else '',
                                         np.mean(diff) * 100))


if __name__ == '__main__':
    # sst2_without_subtrees()
    random_dev_set()
    remove_by_random()
    remove_by_confidence()
    remove_by_similarity()
    # compare_scores(
    #     args_dirs=[
    #         'configs/SST-2/most_similar_to_combined_dev_removed.json',
    #         'configs/SST-2/most_similar_to_negative_dev_removed.json',
    #         'configs/SST-2/most_similar_to_positive_dev_removed.json',
    #         'configs/SST-2/least_similar_to_combined_dev_removed.json',
    #         'configs/SST-2/least_similar_to_negative_dev_removed.json',
    #         'configs/SST-2/least_similar_to_positive_dev_removed.json',
    #         # 'configs/SST-2/random_10_percent_removed_combined/0.json',
    #         # 'configs/SST-2/random_10_percent_removed_positive/0.json',
    #         # 'configs/SST-2/random_10_percent_removed_negative/0.json',
    #         # 'configs/SST-2/most_confident_10_percent_removed_positive.json',
    #         # 'configs/SST-2/most_confident_10_percent_removed_negative.json',
    #         # 'configs/SST-2/least_confident_10_percent_removed_positive.json',
    #         # 'configs/SST-2/least_confident_10_percent_removed_negative.json',
    #     ]
    # )
