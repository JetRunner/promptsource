import argparse
import textwrap

from promptsource.templates import TemplateCollection
from promptsource.utils import get_dataset


parser = argparse.ArgumentParser(description="Process some integers.")
parser.add_argument("dataset_path", type=str, help="path to dataset name")

args = parser.parse_args()
if "templates.yaml" not in args.dataset_path:
    exit()

path = args.dataset_path.split("/")

dataset_name = path[1]
subset_name = path[2] if len(path) == 4 else ""
template_collection = TemplateCollection()

dataset, _ = get_dataset(dataset_name, subset_name)
splits = list(dataset.keys())
dataset = dataset[splits[0]]

dataset_templates = template_collection.get_dataset(dataset_name, subset_name)
template_list = dataset_templates.all_template_names

width = 80
print("DATASET ", args.dataset_path)

# First show all the templates.
for template_name in template_list:
    template = dataset_templates[template_name]
    print("TEMPLATE")
    print("NAME:", template_name)
    print("Is Task Template: ", template.get_task_template())
    print(template.jinja)
    print()

# Show examples of the templates.
for template_name in template_list:
    template = dataset_templates[template_name]
    print()
    print("TEMPLATE")
    print("NAME:", template_name)
    print("REFERENCE:", template.reference)
    print("--------")
    print()
    print(template.jinja)
    print()

    print_counter = 0
    for example in dataset:
        print("\t--------")
        print("\tExample ", example)
        print("\t--------")
        output = template.apply(example)
        if output[0].strip() == "" or (len(output) > 1 and output[1].strip() == ""):
            print("\t Blank result")
            continue

        xp, yp = output
        print()
        print("\tPrompt | X")
        for line in textwrap.wrap(xp, width=width, replace_whitespace=False):
            print("\t", line.replace("\n", "\n\t"))
        print()
        print("\tY")
        for line in textwrap.wrap(yp, width=width, replace_whitespace=False):
            print("\t", line.replace("\n", "\n\t"))

        print_counter += 1
        if print_counter >= 10:
            break
