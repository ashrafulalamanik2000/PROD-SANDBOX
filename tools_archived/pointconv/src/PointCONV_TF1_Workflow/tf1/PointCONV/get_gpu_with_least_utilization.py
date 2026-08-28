import subprocess


def get_gpu_utilization():
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
            stdout=subprocess.PIPE,
            check=True,
            text=True
        )
        utilization = result.stdout.strip().split('\n')
        return [int(usage) for usage in utilization if usage.strip() != '']
    except Exception as e:
        print("Error fetching GPU utilization:", e)
        return None


def get_gpu_with_least_utilization():
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
            stdout=subprocess.PIPE,
            check=True,
            text=True
        )
        utilizations = [int(x) for x in result.stdout.strip().split('\n') if x.strip() != '']
        if not utilizations:
            print("No GPUs found.")
            return None

        min_index = utilizations.index(min(utilizations))
        return min_index

    except Exception as e:
        print("Error fetching GPU utilization:", e)
        return None


if __name__ == "__main__":
    gpu_index = get_gpu_with_least_utilization()
    if gpu_index is not None:
        print("GPU with the least utilization is at index:", gpu_index)
    else:
        print("Could not determine GPU utilization.")

    print("Per GPU utilization (%):", get_gpu_utilization())

