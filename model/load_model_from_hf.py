import pickle

from huggingface_hub import hf_hub_download

path = hf_hub_download(
    repo_id="[ERT REPONAMN]",
    filename="model.pkl",
    revision="v1",
)

with open(path, "rb") as f:
    payload = pickle.load(f)

model = payload["model"]
print(payload["metrics"])
