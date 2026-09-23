from huggingface_hub import HfApi, login

REPO_ID = "mikaelhuss/elpris-se3"
VERSION = "v2"

login()  # lokalt 
api = HfApi()

api.upload_file(
    path_or_fileobj="./model_card.md",
    path_in_repo="README.md", # namnet på filen i HuggingFace Hub
    repo_id=REPO_ID,
    commit_message="Add model card for v2",
)

commit = api.upload_file(
    path_or_fileobj="./model.pkl",
    path_in_repo="model.pkl",
    repo_id=REPO_ID,
    commit_message="Register model v2",
)

api.create_tag(
    repo_id=REPO_ID,
    tag=VERSION,
    revision=commit.oid,
    tag_message="First registered model",
)
