import uuid

files = {}

def save_file(path):
    file_id = str(uuid.uuid4())

    files[file_id] = path

    return file_id


def get_file(file_id):
    return files.get(file_id)