import textwrap
from multiprocessing import Manager, Pool

import pandas as pd
import plotly.express as px
import streamlit as st
from datasets import get_dataset_infos
from jinja2 import TemplateSyntaxError
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import DjangoLexer
from session import _get_state
from utils import get_dataset, get_dataset_confs, list_datasets, removeHyphen, renameDatasetColumn, render_features

from templates import Template, TemplateCollection


#
# Helper functions for datasets library
#

get_dataset = st.cache(allow_output_mutation=True)(get_dataset)
get_dataset_confs = st.cache(get_dataset_confs)


def reset_template_state():
    state.template_name = None
    state.jinja = None
    state.reference = None


#
# Loads session state
#
state = _get_state()

#
# Initial page setup
#
st.set_page_config(layout="wide")
mode = st.sidebar.selectbox(
    label="Choose a mode",
    options=["Helicopter view", "Prompted dataset viewer", "Sourcing"],
    index=0,
    key="mode_select",
)
st.sidebar.title(f"Prompt sourcing 🌸 - {mode}")

#
# Adds pygments styles to the page.
#
st.markdown("<style>" + HtmlFormatter().get_style_defs(".highlight") + "</style>", unsafe_allow_html=True)

WIDTH = 80


def show_jinja(t, width=WIDTH):
    wrap = textwrap.fill(t, width=width, replace_whitespace=False)
    out = highlight(wrap, DjangoLexer(), HtmlFormatter())
    st.write(out, unsafe_allow_html=True)


def show_text(t, width=WIDTH):
    wrap = [textwrap.fill(subt, width=width, replace_whitespace=False) for subt in t.split("\n")]
    wrap = "\n".join(wrap)
    st.write(wrap, unsafe_allow_html=True)


#
# Loads template data
#
try:
    template_collection = TemplateCollection()
except FileNotFoundError:
    st.error(
        "Unable to find the templates folder!\n\n"
        "We expect the folder to be in the working directory. "
        "You might need to restart the app in the root directory of the repo."
    )
    st.stop()


if mode == "Helicopter view":
    st.title("High level metrics")
    st.write(
        "If you want to take ownership for prompting a specific dataset, "
        + "put your name in [this spreadsheet](https://docs.google.com/spreadsheets/d/10SBt96nXutB49H52PV2Lvne7F1NvVr_WZLXD8_Z0JMw/edit?usp=sharing)."
    )

    #
    # Global metrics
    #
    counts = template_collection.get_templates_count()
    nb_prompted_datasets = len(counts)
    st.write(f"## Number of *prompted datasets*: `{nb_prompted_datasets}`")
    nb_prompts = sum(counts.values())
    st.write(f"## Number of *prompts*: `{nb_prompts}`")

    #
    # Metrics per dataset/subset
    #
    # Download dataset infos (multiprocessing download)
    manager = Manager()
    all_infos = manager.dict()
    all_datasets = list(set([t[0] for t in template_collection.keys]))

    def get_infos(d_name):
        all_infos[d_name] = get_dataset_infos(d_name)

    pool = Pool(processes=len(all_datasets))
    pool.map(get_infos, all_datasets)
    pool.close()
    pool.join()

    results = []
    for (dataset_name, subset_name) in template_collection.keys:
        # Collect split sizes (train, validation and test)
        if dataset_name not in all_infos:
            infos = get_dataset_infos(dataset_name)
            all_infos[dataset_name] = infos
        else:
            infos = all_infos[dataset_name]
        if subset_name is None:
            subset_infos = infos[list(infos.keys())[0]]
        else:
            subset_infos = infos[subset_name]

        split_sizes = {k: v.num_examples for k, v in subset_infos.splits.items()}

        # Collect template counts, task template counts and names
        dataset_templates = template_collection.get_dataset(dataset_name, subset_name)
        results.append(
            {
                "Dataset name": dataset_name,
                "Subset name": "" if subset_name is None else subset_name,
                "Train size": split_sizes["train"] if "train" in split_sizes else 0,
                "Validation size": split_sizes["validation"] if "validation" in split_sizes else 0,
                "Test size": split_sizes["test"] if "test" in split_sizes else 0,
                "Number of templates": len(dataset_templates),
                "Number of task templates": sum([t.get_task_template() for t in dataset_templates.templates.values()]),
                "Template names": [t.name for t in dataset_templates.templates.values()],
                # TODO: template name is not very informative... refine that
            }
        )
    results_df = pd.DataFrame(results)
    results_df.sort_values(["Number of templates"], inplace=True, ascending=False)
    results_df.reset_index(drop=True, inplace=True)

    nb_training_instances = results_df["Train size"].sum()
    st.write(f"## Number of *training instances*: `{nb_training_instances}`")

    plot_df = results_df[["Dataset name", "Subset name", "Train size", "Number of templates"]].copy()
    plot_df["Name"] = plot_df["Dataset name"] + " - " + plot_df["Subset name"]
    plot_df.sort_values(["Train size"], inplace=True, ascending=False)
    fig = px.bar(
        plot_df,
        x="Name",
        y="Train size",
        hover_data=["Dataset name", "Subset name", "Number of templates"],
        log_y=True,
        title="Number of training instances per data(sub)set - y-axis is in logscale",
    )
    fig.update_xaxes(visible=False, showticklabels=False)
    st.plotly_chart(fig, use_container_width=True)
    st.write(
        f"- Top 3 training subsets account for `{100*plot_df[:3]['Train size'].sum()/nb_training_instances:.2f}%` of the training instances."
    )
    biggest_training_subset = plot_df.iloc[0]
    st.write(
        f"- Biggest training subset is *{biggest_training_subset['Name']}* with `{biggest_training_subset['Train size']}` instances"
    )
    smallest_training_subset = plot_df[plot_df["Train size"] > 0].iloc[-1]
    st.write(
        f"- Smallest training subset is *{smallest_training_subset['Name']}* with `{smallest_training_subset['Train size']}` instances"
    )

    st.markdown("***")
    st.write("Details per dataset")
    st.table(results_df)

else:
    # Combining mode `Prompted dataset viewer` and `Sourcing` since the
    # backbone of the interfaces is the same
    assert mode in ["Prompted dataset viewer", "Sourcing"], ValueError(
        f"`mode` ({mode}) should be in `[Helicopter view, Prompted dataset viewer, Sourcing]`"
    )

    #
    # Loads dataset information
    #
    if mode == "Prompted dataset viewer":
        priority_filter = False
        priority_max_templates = None
    else:  # mode = Sourcing
        priority_filter = st.sidebar.checkbox(
            "Filter Priority Datasets", help="This filter surfaces datasets with less than X prompts."
        )
        if priority_filter:
            priority_max_templates = st.sidebar.number_input(
                "Max no of templates per dataset", min_value=1, max_value=50, value=2, step=1
            )
        else:
            # Clear working priority dataset retained in the
            # priority list with more than priority_max_templates
            state.working_priority_ds = None
            priority_max_templates = None

    dataset_list = list_datasets(
        template_collection,
        priority_filter,
        priority_max_templates,
        state,
    )

    #
    # Select a dataset - starts with ag_news
    #
    dataset_key = st.sidebar.selectbox(
        "Dataset",
        dataset_list,
        key="dataset_select",
        index=12,  # AG_NEWS
        help="Select the dataset to work on.",
    )

    if mode == "Sourcing":
        # On dataset change, clear working priority dataset
        # retained in the priority list with more than priority_max_templates
        if dataset_key != state.working_priority_ds:
            state.working_priority_ds = None

    #
    # If a particular dataset is selected, loads dataset and template information
    #
    if dataset_key is not None:

        #
        # Check for subconfigurations (i.e. subsets)
        #
        configs = get_dataset_confs(dataset_key)
        conf_option = None
        if len(configs) > 0:
            conf_option = st.sidebar.selectbox("Subset", configs, index=0, format_func=lambda a: a.name)

        dataset, failed = get_dataset(dataset_key, str(conf_option.name) if conf_option else None)
        if failed:
            if dataset.manual_download_instructions is not None:
                st.error(f"Dataset {dataset_key} requires manual download. Please skip for the moment.")
            else:
                st.error(f"Loading dataset {dataset_key} failed.\n{dataset}. Please skip for the moment.")

        splits = list(dataset.keys())
        index = 0
        if "train" in splits:
            index = splits.index("train")
        split = st.sidebar.selectbox("Split", splits, key="split_select", index=index)
        dataset = dataset[split]
        dataset = renameDatasetColumn(dataset)

        dataset_templates = template_collection.get_dataset(dataset_key, conf_option.name if conf_option else None)

        template_list = dataset_templates.all_template_names
        num_templates = len(template_list)
        st.sidebar.write(
            "No of Templates created for "
            + f"`{dataset_key + (('/' + conf_option.name) if conf_option else '')}`"
            + f": **{str(num_templates)}**"
        )

        if mode == "Prompted dataset viewer":
            if num_templates > 0:
                template_name = st.sidebar.selectbox(
                    "Template name",
                    template_list,
                    key="template_select",
                    index=0,
                    help="Select the template to visualize.",
                )

            step = 50
            example_index = st.sidebar.number_input(
                f"Select the example index (Size = {len(dataset)})",
                min_value=0,
                max_value=len(dataset) - step,
                value=0,
                step=step,
                key="example_index_number_input",
                help="Offset = 50.",
            )
        else:  # mode = Sourcing
            st.sidebar.subheader("Select Example")
            example_index = st.sidebar.slider("Select the example index", 0, len(dataset) - 1)

            example = dataset[example_index]
            example = removeHyphen(example)

            st.sidebar.write(example)

        st.sidebar.subheader("Dataset Schema")
        st.sidebar.write(render_features(dataset.features))

        #
        # Display dataset information
        #
        st.header("Dataset: " + dataset_key + " " + (("/ " + conf_option.name) if conf_option else ""))

        st.markdown(
            "*Homepage*: "
            + dataset.info.homepage
            + "\n\n*Dataset*: https://github.com/huggingface/datasets/blob/master/datasets/%s/%s.py"
            % (dataset_key, dataset_key)
        )

        md = """
        %s
        """ % (
            dataset.info.description.replace("\\", "") if dataset_key else ""
        )
        st.markdown(md)

        #
        # Body of the app: display prompted examples in mode `Prompted dataset viewer`
        # or text boxes to create new prompts in mode `Sourcing`
        #
        if mode == "Prompted dataset viewer":
            #
            # Display template information
            #
            if num_templates > 0:
                template = dataset_templates[template_name]
                st.subheader("Template")
                st.markdown("##### Name")
                st.text(template.name)
                st.markdown("##### Reference")
                st.text(template.reference)
                st.markdown("##### Task Template? ")
                st.text(template.get_task_template())
                st.markdown("##### Jinja")
                splitted_template = template.jinja.split("|||")
                st.markdown("###### Prompt + X")
                show_jinja(splitted_template[0])
                if len(splitted_template) > 1:
                    st.markdown("###### Y")
                    show_jinja(splitted_template[1])
                st.markdown("***")

            #
            # Display a couple (steps) examples
            #
            for ex_idx in range(example_index, example_index + step):
                if ex_idx >= len(dataset):
                    continue
                example = dataset[ex_idx]
                example = removeHyphen(example)
                col1, _, col2 = st.beta_columns([12, 1, 12])
                with col1:
                    st.write(example)
                if num_templates > 0:
                    with col2:
                        try:
                            prompt = template.apply(example, highlight_variables=True)
                        except (TemplateSyntaxError, TypeError):
                            prompt = template.apply(example, highlight_variables=False)
                        if prompt == [""]:
                            st.write("∅∅∅ *Blank result*")
                        else:
                            st.write("Prompt + X")
                            show_text(prompt[0])
                            if len(prompt) > 1:
                                st.write("Y")
                                show_text(prompt[1])
                st.markdown("***")
        else:  # mode = Sourcing
            st.markdown("## Template Creator")

            #
            # Create a new template or select an existing one
            #
            col1a, col1b, _, col2 = st.beta_columns([9, 9, 1, 6])

            # current_templates_key and state.templates_key are keys for the templates object
            current_templates_key = (dataset_key, conf_option.name if conf_option else None)

            # Resets state if there has been a change in templates_key
            if state.templates_key != current_templates_key:
                state.templates_key = current_templates_key
                reset_template_state()

            with col1a, st.form("new_template_form"):
                new_template_name = st.text_input(
                    "Create a New Template",
                    key="new_template",
                    value="",
                    help="Enter name and hit enter to create a new template.",
                )
                new_template_submitted = st.form_submit_button("Create")
                if new_template_submitted:
                    if new_template_name in dataset_templates.all_template_names:
                        st.error(
                            f"A template with the name {new_template_name} already exists "
                            f"for dataset {state.templates_key}."
                        )
                    elif new_template_name == "":
                        st.error("Need to provide a template name.")
                    else:
                        template = Template(new_template_name, "", "")
                        dataset_templates.add_template(template)
                        reset_template_state()
                        state.template_name = new_template_name
                        # Keep the current working dataset in priority list
                        if priority_filter:
                            state.working_priority_ds = dataset_key
                else:
                    state.new_template_name = None

            with col1b, st.beta_expander("or Select Template", expanded=True):
                dataset_templates = template_collection.get_dataset(*state.templates_key)
                template_list = dataset_templates.all_template_names
                if state.template_name:
                    index = template_list.index(state.template_name)
                else:
                    index = 0
                state.template_name = st.selectbox(
                    "", template_list, key="template_select", index=index, help="Select the template to work on."
                )

                if st.button("Delete Template", key="delete_template"):
                    dataset_templates.remove_template(state.template_name)
                    reset_template_state()

            variety_guideline = """
            :heavy_exclamation_mark::question:Creating a diverse set of prompts whose differences go beyond surface wordings (i.e. marginally changing 2 or 3 words) is highly encouraged.
            Ultimately, the hope is that exposing the model to such a diversity will have a non-trivial impact on the model's robustness to the prompt formulation.
            \r**To get various prompts, you can try moving the cursor along theses axes**:
            \n- **Interrogative vs affirmative form**: Ask a question about an attribute of the inputs or tell the model to decide something about the input.
            \n- **Task description localization**: where is the task description blended with the inputs? In the beginning, in the middle, at the end?
            \n- **Implicit situation or contextualization**: how explicit is the query? For instance, *Given this review, would you buy this product?* is an indirect way to ask whether the review is positive.
            """

            col1, _, _ = st.beta_columns([18, 1, 6])
            with col1:
                if state.template_name is not None:
                    show_text(variety_guideline)

            #
            # Edit the created or selected template
            #
            col1, _, col2 = st.beta_columns([18, 1, 6])
            with col1:
                if state.template_name is not None:
                    template = dataset_templates[state.template_name]
                    #
                    # If template is selected, displays template editor
                    #
                    with st.form("edit_template_form"):
                        updated_template_name = st.text_input("Name", value=template.name)
                        state.reference = st.text_input(
                            "Template Reference",
                            help="Short description of the template and/or paper reference for the template.",
                            value=template.reference,
                        )
                        state.task_template = st.checkbox(
                            "Task Template?",
                            value=template.get_task_template(),
                            help="Task templates correspond one-to-one with the original task.",
                        )
                        state.jinja = st.text_area("Template", height=40, value=template.jinja)

                        if st.form_submit_button("Save"):
                            if (
                                updated_template_name in dataset_templates.all_template_names
                                and updated_template_name != state.template_name
                            ):
                                st.error(
                                    f"A template with the name {updated_template_name} already exists "
                                    f"for dataset {state.templates_key}."
                                )
                            elif updated_template_name == "":
                                st.error("Need to provide a template name.")
                            else:
                                dataset_templates.update_template(
                                    state.template_name,
                                    updated_template_name,
                                    state.jinja,
                                    state.reference,
                                    state.task_template,
                                )
                                # Update the state as well
                                state.template_name = updated_template_name
            #
            # Displays template output on current example if a template is selected
            # (in second column)
            #
            with col2:
                if state.template_name is not None:
                    st.empty()
                    template = dataset_templates[state.template_name]
                    prompt = template.apply(example)
                    if prompt == [""]:
                        st.write("∅∅∅ *Blank result*")
                    else:
                        st.write("Prompt + X")
                        show_text(prompt[0], width=40)
                        if len(prompt) > 1:
                            st.write("Y")
                            show_text(prompt[1], width=40)


#
# Must sync state at end
#
state.sync()
