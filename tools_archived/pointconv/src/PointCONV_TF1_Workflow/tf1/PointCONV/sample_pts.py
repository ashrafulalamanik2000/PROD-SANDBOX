from scipy.spatial import cKDTree
from joblib import Parallel, delayed
from tqdm import tqdm  # Optional: for progress monitoring

import logging

import numpy as np
from collections import defaultdict


def divide_into_regions_with_min_pts(xy, num_regions, min_pts):
    """
    Divide the 2D points `xy` into spatial grid regions, ensuring each region
    has at least `min_pts` and every point in `xy` is included in at least one region.
    Regions are allowed to overlap.

    Parameters:
        xy (np.ndarray): Array of 2D points (shape: Nx2).
        num_regions (int): Number of regions (should yield a grid of size sqrt(num_regions)).
        min_pts (int): Minimum number of points required in each region.

    Returns:
        dict: A mapping of region indices to the point indices in that region.
    """
    # Step 1: Determine grid size
    grid_size = int(np.ceil(np.sqrt(num_regions)))

    # Step 2: Define bin edges for grid
    x_bins = np.linspace(np.min(xy[:, 0]), np.max(xy[:, 0]), grid_size + 1)
    y_bins = np.linspace(np.min(xy[:, 1]), np.max(xy[:, 1]), grid_size + 1)

    # Step 3: Assign points to regions (grid cells)
    x_bin_indices = np.digitize(xy[:, 0], x_bins) - 1
    y_bin_indices = np.digitize(xy[:, 1], y_bins) - 1
    region_indices = list(zip(x_bin_indices, y_bin_indices))

    # Step 4: Initialize dictionary to group points by region
    regions = defaultdict(list)
    for idx, region in enumerate(region_indices):
        regions[region].append(idx)

    # Step 5: Ensure each region has at least `min_pts`
    for region in list(regions.keys()):
        if len(regions[region]) < min_pts:
            k_add = min_pts - len(regions[region])
            remaining_points_indices = np.setdiff1d(range(len(xy)), regions[region])
            remaining_points = xy[remaining_points_indices]

            # Find k nearest remaining points to this region
            region_points = xy[regions[region]]
            if len(region_points) > 0:
                region_center = np.mean(region_points, axis=0)
            else:
                # If no points exist in this region, use bin center as fallback
                x_bin_idx, y_bin_idx = region
                region_center = np.array([
                    (x_bins[x_bin_idx] + x_bins[x_bin_idx + 1]) / 2,
                    (y_bins[y_bin_idx] + y_bins[y_bin_idx + 1]) / 2,
                ])

            distances = np.linalg.norm(remaining_points - region_center, axis=1)
            closest_indices = remaining_points_indices[np.argsort(distances)[:k_add]]
            regions[region].extend(closest_indices)

    # Step 6: Ensure all points are covered
    all_points = set(range(len(xy)))
    covered_points = set(idx for points in regions.values() for idx in points)
    uncovered_points = all_points - covered_points

    if uncovered_points:
        for point in uncovered_points:
            point_coords = xy[point]
            # Assign point to the nearest region
            closest_region = None
            closest_distance = float("inf")
            for region, indices in regions.items():
                region_points = xy[indices]
                region_center = np.mean(region_points, axis=0)
                distance = np.linalg.norm(point_coords - region_center)
                if distance < closest_distance:
                    closest_region = region
                    closest_distance = distance
            regions[closest_region].append(point)

    # Step 7: Sort region indices for consistent output
    return {region: sorted(points) for region, points in regions.items()}


def weighted_sampling(neighbors, deficits, k):
    """
    Select exactly `k` points from `neighbors`, weighted by their deficits.

    Parameters:
        neighbors (np.ndarray): Array of neighbor point indices.
        deficits (np.ndarray): Array of deficits (q - coverage[neighbors]) for each neighbor.
        k (int): Number of points to sample.

    Returns:
        np.ndarray: Array of exactly `k` selected neighbor indices.
    """
    neighbors_arr = np.array(neighbors)
    deficits = np.array(deficits)

    # Identify neighbors with non-zero deficits
    positive_deficits = deficits > 0
    num_positive = np.count_nonzero(positive_deficits)

    if num_positive >= k:
        # If enough neighbors have positive deficits, sample using their weights
        probabilities = deficits / deficits.sum()
        selected = np.random.choice(neighbors_arr, size=k, replace=False, p=probabilities)
    else:
        # Not enough neighbors with positive deficits; combine positive and uniform sampling
        selected_positive = neighbors_arr[positive_deficits]

        if num_positive > 0:
            positive_probs = deficits[positive_deficits] / deficits[positive_deficits].sum()
            selected_positive = np.random.choice(selected_positive, size=num_positive, replace=False, p=positive_probs)

        remaining = k - len(selected_positive)
        zero_deficit_neighbors = neighbors_arr[~positive_deficits]

        if remaining > 0:
            selected_zero = np.random.choice(zero_deficit_neighbors, size=remaining, replace=False)
            selected = np.concatenate([selected_positive, selected_zero])
        else:
            selected = selected_positive  # All sampled points are deficit-biased

    return selected


def sample_groups_region(xy, ind, r, k, q, num_candidates_in, num_threads):
    xy_region = xy[ind]

    groups, coverages = sample_groups(xy_region, r, k, q,
                                      num_candidates_in,
                                      num_threads)

    region_coverage = np.zeros(len(xy), dtype=int)
    region_coverage[ind] = coverages
    region_group = []

    ind_ref = np.array(ind)
    for i in range(len(groups)):
        region_group.append(ind_ref[groups[i]])

    return region_group, region_coverage


def sample_groups(xy, r, k, q, num_candidates_in=50, num_threads=8):
    """
    Partition the points in xy into groups, each with exactly k points.

    For each candidate center (chosen from points that still need additional coverage),
    the effective radius is set to max(r, distance to kth nearest neighbor) so that
    at least k points fall inside. If more than k points are present in the circle,
    exactly k points are selected via a weighted random sample that favors those points
    that have lower coverage (i.e. larger deficit q - coverage). The candidate’s score is
    computed as the sum of deficits for the selected points.

    The candidate with the highest score is chosen in each iteration and its selected
    k points form a new group. The process continues until every point is included in
    at least q groups.

    Parameters
    ----------
    xy : np.ndarray of shape (N, 2)
         Coordinates of N points.
    r : float
         Minimum allowed circle radius.
    k : int
         Exact number of points per group (typically 1024 or larger).
    q : int
         Each point must appear in at least q groups.
    num_candidates : int, optional
         Number of candidate centers to consider in each iteration.
    num_threads : int, optional
         Number of threads to use in parallel candidate evaluation.

    Returns
    -------
    groups : list of np.ndarray
         A list where each element is an array of exactly k indices (into xy) forming one group.

    Raises
    ------
    ValueError
         If no candidate center can yield at least k points.
    """
    N = len(xy)
    coverage = np.zeros(N, dtype=int)  # How many groups each point is included in.
    groups = []
    tree = cKDTree(xy)
    iteration = 0

    # Optional: Set up a progress bar.
    pbar = tqdm(total=N, desc="Points with required coverage", unit="point")

    def evaluate_candidate(i):
        """
        Evaluate candidate center at index i.

        Returns a tuple (score, candidate_index, selected) where:
          - score: Total deficit (q - current_coverage) over the selected points, or -1 if candidate is invalid.
          - candidate_index: The candidate point's index.
          - selected: An array of exactly k indices (into xy) forming the candidate group.
        """
        # Compute the effective radius: at least r and at least the kth nearest neighbor distance.
        # if k > 1:
        #     distances, _ = tree.query(xy[i], k=k)
        #     candidate_radius = distances[k - 1]  # kth nearest neighbor distance.
        # else:
        #     candidate_radius = r
        #
        # effective_radius = max(r, candidate_radius)
        # neighbors = tree.query_ball_point(xy[i], effective_radius)
        # if len(neighbors) < k:
        #     return -1, i, None  # Not enough points for a full group.

        neighbors = tree.query_ball_point(xy[i], r)
        if len(neighbors) < k:
            distances, _ = tree.query(xy[i], k=k)
            candidate_radius = distances[k - 1]  # kth nearest neighbor distance.

            neighbors = tree.query_ball_point(xy[i], candidate_radius)
            if len(neighbors) < k:
                return -1, i, None  # Not enough points for a full group.

        # Bias the selection toward points with lower coverage.
        # Compute the deficit for each neighbor: how many more groups are needed.
        deficits = np.maximum(q - coverage[neighbors], 0).astype(np.float64)
        neighbors_arr = np.array(neighbors)

        # Count how many neighbors have a positive deficit.
        num_nonzero = np.count_nonzero(deficits)

        if num_nonzero >= k:
            # If there are at least k neighbors with positive deficits,
            # sample using the weighted probabilities.
            p = deficits / deficits.sum()
            try:
                selected = np.random.choice(neighbors_arr, size=k, replace=False, p=p)
            except ValueError as e:
                # Fallback to uniform sampling if an error occurs.
                selected = np.random.choice(neighbors_arr, size=k, replace=False)
        else:
            # Not enough neighbors have a positive deficit.
            # Sample all the ones with nonzero deficit using weighting...
            if num_nonzero > 0:
                nonzero_neighbors = neighbors_arr[deficits > 0]
                nonzero_deficits = deficits[deficits > 0]
                selected_nonzero = np.random.choice(nonzero_neighbors, size=len(nonzero_neighbors),
                                                    replace=False,
                                                    p=nonzero_deficits / nonzero_deficits.sum())
            else:
                selected_nonzero = np.array([], dtype=int)
            # Then sample the remaining ones uniformly from the zero-deficit neighbors.
            zero_neighbors = neighbors_arr[deficits == 0]
            remaining = k - len(selected_nonzero)
            if remaining > len(zero_neighbors):
                # Should not happen because len(neighbors) >= k, but as a safety fallback.
                selected_zero = np.random.choice(zero_neighbors, size=remaining, replace=True)
            else:
                selected_zero = np.random.choice(zero_neighbors, size=remaining, replace=False)
            selected = np.concatenate([selected_nonzero, selected_zero])

        # Compute the candidate's score as the total deficit among the selected points.
        sel_deficit = np.maximum(q - coverage[selected], 0)
        score = sel_deficit.sum()
        return score, i, selected

    num_candidates = num_candidates_in
    num_candidates_updated_num = 0
    while np.any(coverage < q):
        iteration += 1
        undercovered = np.where(coverage < q)[0]
        if len(undercovered) == 0:
            break

        # Print progress info (optional)
        num_fully_covered = np.count_nonzero(coverage >= q)
        progress_percent = (num_fully_covered / N) * 100
        # logging.info(f"Iteration {iteration}: {num_fully_covered}/{N} points fully covered ({progress_percent:.2f}%).")

        if len(undercovered) > num_candidates:
            ind_search = np.where(coverage == np.min(coverage))[0]
            candidate_indices = np.random.choice(ind_search,
                                                 size=np.min([num_candidates, ind_search.shape[0]]),
                                                 replace=False)
            # candidate_indices = np.random.choice(undercovered, size=num_candidates, replace=False)
        else:
            candidate_indices = undercovered

        if num_threads > 1:
            # Evaluate candidates in parallel using Joblib.
            results = Parallel(n_jobs=num_threads)(
                delayed(evaluate_candidate)(i) for i in candidate_indices
            )
        else:
            results = [evaluate_candidate(i) for i in candidate_indices]

        best_score = -1
        best_candidate = None
        best_selected = None

        for score, candidate, selected in results:
            if score > best_score and selected is not None:
                best_score = score
                best_candidate = candidate
                best_selected = selected

        if best_candidate is None:
            logging.info(
                f"No candidates found> Iteration {iteration}: {num_fully_covered}/{N} points fully covered ({progress_percent:.2f}%).")
            logging.info(
                f"    Number of points found {num_fully_covered} out of {N} points: Points left  fully covered ({progress_percent:.2f}%).")
            num_candidates = N - num_fully_covered
            if num_candidates_updated_num < 1:
                num_candidates = N - num_fully_covered

                logging.info(f"    Rerunning one more time with num_candidates = {num_candidates}%).")
                num_candidates_updated_num += 1
            else:
                logging.info(f"    Stopping with uncovered candidates = {num_candidates}%).")
                break
        else:
            num_candidates = num_candidates_in
            num_candidates_updated_num = 0

            # Form the group and update coverage counts.
            groups.append(best_selected)
            coverage[best_selected] += 1

            # Update the progress bar.
            current_fully_covered = np.count_nonzero(coverage >= q)
            pbar.n = current_fully_covered
            pbar.refresh()

    pbar.close()
    return np.array(groups), coverage


def sample_groups_1(xy, r, k, q, num_candidates_in, num_threads):
    N = len(xy)
    coverage = np.zeros(N, dtype=int)
    groups = []
    tree = cKDTree(xy)
    pbar = tqdm(total=N, desc="Points with required coverage", unit="point")

    while np.any(coverage < q):
        undercovered = np.where(coverage < q)[0]
        deficits = q - coverage  # Compute deficits
        deficits[deficits < 0] = 0  # Optional: Prevent negative deficits

        candidate_indices = (
            np.random.choice(undercovered, size=min(num_candidates_in, len(undercovered)), replace=False)
        )
        results = Parallel(n_jobs=num_threads)(
            delayed(evaluate_candidate)(i, tree, xy, r, k, q, coverage, deficits) for i in candidate_indices
        ) if num_threads > 1 else [
            evaluate_candidate(i, tree, xy, r, k, q, coverage, deficits) for i in candidate_indices
        ]
        best_score, best_selected = max(results, key=lambda x: x[0])
        if best_score > 0:
            groups.append(best_selected)
            coverage[best_selected] += 1
            pbar.update(np.count_nonzero(coverage >= q) - pbar.n)
        else:
            logging.warning("No valid candidates found. Stopping early.")
            break

    pbar.close()
    return np.array(groups), coverage


def evaluate_candidate(index, tree, xy, r, k, q, coverage, deficits):
    # Find neighbors within radius
    neighbors = tree.query_ball_point(xy[index], r)

    if len(neighbors) < k:  # Not enough points
        return 0, None

    # Random selection or advanced logic for selecting points
    selected = np.random.choice(neighbors, k, replace=False)

    # Example of correcting broadcasting issue:
    try:
        valid_indices = (selected >= 0) & (selected < len(deficits))  # Valid range check
        selected = selected[valid_indices]

        # Score calculation using deficits
        score = deficits[selected].sum()  # Adjusted to work directly with selected indices
        return score, selected

    except IndexError:
        # logging.error(
        #     f"Indexing error at index {index}. Check shapes: selected={selected.shape}, deficits={deficits.shape}")
        return 0, None


def visualize_regions(xy, regions, x_bins, y_bins):
    import matplotlib.pyplot as plt

    """
    Visualize the regions and the points in each region.

    Parameters:
        xy (np.ndarray): Array of 2D points (shape: Nx2).
        regions (dict): Dictionary mapping region indices to point indices.
        x_bins (np.ndarray): Bin edges for the x-coordinate.
        y_bins (np.ndarray): Bin edges for the y-coordinate.

    Returns:
        None: Displays a visualization of points and regions.
    """
    # Step 1: Create a scatter plot of all points
    plt.figure(figsize=(10, 10))
    plt.scatter(xy[:, 0], xy[:, 1], c='blue', s=20, label='Points')

    # Step 2: Draw the grid lines using the bin edges
    for x in x_bins:
        plt.axvline(x, color='gray', linestyle='--', linewidth=1)
    for y in y_bins:
        plt.axhline(y, color='gray', linestyle='--', linewidth=1)

    # Step 3: Annotate each region with its key
    for region, point_indices in regions.items():
        # Decode the region index into x- and y-bin indices
        x_bin_idx = region // (len(y_bins) - 1)  # Safe division for x-bin
        y_bin_idx = region % (len(y_bins) - 1)  # Safe division for y-bin

        # Clamp indices to avoid accessing out-of-bound elements
        x_bin_idx = min(x_bin_idx, len(x_bins) - 2)
        y_bin_idx = min(y_bin_idx, len(y_bins) - 2)

        # Calculate the center of this region
        region_center_x = (x_bins[x_bin_idx] + x_bins[x_bin_idx + 1]) / 2
        region_center_y = (y_bins[y_bin_idx] + y_bins[y_bin_idx + 1]) / 2

        # Annotate the center with the region key
        plt.text(region_center_x, region_center_y, f'{region}', color='red',
                 fontsize=10, ha='center', va='center', bbox=dict(facecolor='white', alpha=0.6))

    # Step 4: Customize the visualization
    plt.xlabel("X Coordinate")
    plt.ylabel("Y Coordinate")
    plt.title("Visualization of Spatial Grid Regions")
    plt.legend()
    plt.grid(False)  # Disable the default grid (only show the custom bin lines)

    # Step 5: Show the plot
    plt.show()


def visualize_regions_per_region(xy, regions, x_bins, y_bins):
    import matplotlib.pyplot as plt

    """
    Visualize each region in a separate plot, showing grid boundaries and points in that region.

    Parameters:
        xy (np.ndarray): Array of 2D points (shape: Nx2).
        regions (dict): Dictionary mapping region indices to point indices.
        x_bins (np.ndarray): Bin edges for the x-coordinate.
        y_bins (np.ndarray): Bin edges for the y-coordinate.

    Returns:
        None: Displays one plot for each region.
    """
    # Step 1: Iterate through each region
    for region, point_indices in regions.items():
        # Decode the region index into x- and y-bin indices
        x_bin_idx = region // (len(y_bins) - 1)  # Safe division for x-bin
        y_bin_idx = region % (len(y_bins) - 1)  # Safe division for y-bin

        # Clamp indices to avoid accessing out-of-bound elements
        x_bin_idx = min(x_bin_idx, len(x_bins) - 2)
        y_bin_idx = min(y_bin_idx, len(y_bins) - 2)

        # Get the points in this region
        region_points = xy[point_indices]

        # Plot the points in the region
        plt.figure(figsize=(6, 6))
        plt.scatter(region_points[:, 0], region_points[:, 1], c='blue', s=20, label='Points in Region')

        # Step 2: Draw the grid boundaries for this region
        plt.axvline(x=x_bins[x_bin_idx], color='gray', linestyle='--', linewidth=1)
        plt.axvline(x=x_bins[x_bin_idx + 1], color='gray', linestyle='--', linewidth=1)
        plt.axhline(y=y_bins[y_bin_idx], color='gray', linestyle='--', linewidth=1)
        plt.axhline(y=y_bins[y_bin_idx + 1], color='gray', linestyle='--', linewidth=1)

        # Step 3: Plot additional context (e.g., region identifier)
        region_center_x = (x_bins[x_bin_idx] + x_bins[x_bin_idx + 1]) / 2
        region_center_y = (y_bins[y_bin_idx] + y_bins[y_bin_idx + 1]) / 2
        plt.text(region_center_x, region_center_y, f'Region {region}', color='red',
                 fontsize=12, ha='center', va='center', bbox=dict(facecolor='white', alpha=0.6))

        # Customize the visualization
        plt.xlabel("X Coordinate")
        plt.ylabel("Y Coordinate")
        plt.title(f"Points in Region {region}")
        plt.legend()
        plt.grid(False)

        # Show the plot for this region
        plt.show()


def plot_convex_hulls(xy, regions, x_bins=None, y_bins=None):
    """
    Plots convex hulls for each region, verifying dimensionality constraints.

    Args:
        xy: np.ndarray of point coordinates (shape: Nx2).
        regions: dict mapping region labels to lists of point indices.
        x_bins, y_bins: Optional bin edges for visual boundaries (not required for convex hull).
    """
    import matplotlib.pyplot as plt
    from scipy.spatial import ConvexHull

    for region, point_indices in regions.items():
        if len(point_indices) > 2:  # Convex Hull is valid only for 3 or more points
            region_points = xy[point_indices]

            # Check if region_points are collinear
            if np.linalg.matrix_rank(region_points - region_points[0]) < 2:
                print(f"Region {region} points are collinear or degenerate. Skipping.")
                continue

            try:
                # Generate Convex Hull
                region_hull = ConvexHull(region_points)

                # Plot the convex hull for the region
                for simplex in region_hull.simplices:
                    plt.plot(region_points[simplex, 0], region_points[simplex, 1], 'b-')
            except Exception as e:
                print(f"Skipping region {region} due to ConvexHull error: {e}")

        else:
            print(f"Region {region} has insufficient points ({len(point_indices)}). Skipping.")

    # Optionally add gridlines if x_bins and y_bins are provided
    if x_bins is not None and y_bins is not None:
        for x_bin in x_bins:
            plt.axvline(x=x_bin, color='gray', linestyle='--', linewidth=0.5)
        for y_bin in y_bins:
            plt.axhline(y=y_bin, color='gray', linestyle='--', linewidth=0.5)

    plt.xlabel('X')
    plt.ylabel('Y')
    plt.title('Convex Hull Visualization')
    plt.show()


def plot_convex_hulls_per_region(xy, regions, x_bins=None, y_bins=None):
    """
    Generates one plot per region, visualizing the convex hull of points in that region.

    Args:
        xy: np.ndarray of point coordinates (shape: Nx2).
        regions: dict mapping region labels to lists of point indices.
        x_bins, y_bins: Optional bin edges to show grid boundaries (not required for convex hull).
    """
    import matplotlib.pyplot as plt
    from scipy.spatial import ConvexHull

    for region, point_indices in regions.items():
        if len(point_indices) > 2:  # Convex Hull is valid only for 3 or more points
            region_points = xy[point_indices]

            # Check if region_points are collinear
            if np.linalg.matrix_rank(region_points - region_points[0]) < 2:
                print(f"Region {region} points are collinear or degenerate. Skipping.")
                continue

            try:
                # Generate Convex Hull
                region_hull = ConvexHull(region_points)

                # Create a new plot for each region
                plt.figure()  # Start a new figure
                plt.scatter(region_points[:, 0], region_points[:, 1], color='red', label='Points')
                for simplex in region_hull.simplices:
                    plt.plot(region_points[simplex, 0], region_points[simplex, 1], 'b-', label='Convex Hull')

                # Optionally add gridlines if x_bins and y_bins are provided
                if x_bins is not None and y_bins is not None:
                    for x_bin in x_bins:
                        plt.axvline(x=x_bin, color='gray', linestyle='--', linewidth=0.5)
                    for y_bin in y_bins:
                        plt.axhline(y=y_bin, color='gray', linestyle='--', linewidth=0.5)

                plt.xlabel('X')
                plt.ylabel('Y')
                plt.title(f'Region {region} Convex Hull')
                plt.legend(loc='upper right')
                plt.show()

            except Exception as e:
                print(f"Skipping region {region} due to ConvexHull error: {e}")

        else:
            print(f"Region {region} has insufficient points ({len(point_indices)}). Skipping.")


#
#
# def divide_into_regions_with_min_pts(xy, num_regions, min_pts):
#     """
#     Divide the 2D points `xy` into spatial grid regions, ensuring each region
#     has at least `min_pts`. Every point in `xy` is included in at least one region.
#     Regions are allowed to overlap.
#
#     Parameters:
#         xy (np.ndarray): Array of 2D points (shape: Nx2).
#         num_regions (int): Number of regions (grid cells).
#         min_pts (int): Minimum number of points required in each region.
#
#     Returns:
#         dict: A mapping of region indices to the point indices in that region.
#     """
#     # Step 1: Determine grid size from the number of regions
#     grid_size = int(np.ceil(np.sqrt(num_regions)))
#
#     # Step 2: Define adaptive histogram binning
#     x_bins = np.linspace(np.min(xy[:, 0]), np.max(xy[:, 0]), grid_size + 1)
#     y_bins = np.linspace(np.min(xy[:, 1]), np.max(xy[:, 1]), grid_size + 1)
#
#     # Step 3: Assign points to bins
#     x_bin_indices = np.digitize(xy[:, 0], x_bins) - 1
#     y_bin_indices = np.digitize(xy[:, 1], y_bins) - 1
#
#     # Step 4: Combine bin indices into tuples for unique regions
#     region_indices = list(zip(x_bin_indices, y_bin_indices))
#
#     # Step 5: Group points by region using a dictionary
#     regions = defaultdict(list)
#     for idx, region in enumerate(region_indices):
#         regions[region].append(idx)
#
#     # Step 6: Ensure each region has at least `min_pts`
#     for region in list(regions.keys()):
#         # Ensure the region has at least the minimum required points
#         if len(regions[region]) < min_pts:
#             k_add = min_pts - len(regions[region])
#             remaining_points_indices = np.setdiff1d(range(len(xy)), regions[region], assume_unique=True)
#             remaining_points = xy[remaining_points_indices]
#
#             # Fetch points currently in the region
#             region_points = np.array([xy[i] for i in regions[region]])
#
#             # Determine region center
#             if len(region_points) >= 3 and np.linalg.matrix_rank(region_points - region_points[0]) >= 2:
#                 # Use convex hull if points form a full-dimensional set
#                 try:
#                     hull = ConvexHull(region_points)
#                     region_center = np.mean(region_points[hull.vertices], axis=0)
#                 except (QhullError, ValueError):
#                     # Fallback to centroid if ConvexHull fails
#                     region_center = np.mean(region_points, axis=0)
#             else:
#                 # Use simple mean for fewer points or insufficient dimensions
#                 region_center = np.mean(region_points, axis=0)
#
#             # Find the nearest remaining points to augment the region
#             distances = np.linalg.norm(remaining_points - region_center, axis=1)
#             closest_indices = remaining_points_indices[np.argsort(distances)[:k_add]]
#             regions[region].extend(closest_indices)
#
#     # Step 7: Ensure every point is part of at least one region
#     points_covered = set()
#     for points in regions.values():
#         points_covered.update(points)
#
#     uncovered_points = set(range(len(xy))) - points_covered
#     for point in uncovered_points:
#         # Assign this point to the closest region (based on centers)
#         point_coords = xy[point]
#         closest_region = None
#         closest_distance = float("inf")
#
#         # Find the closest region based on center distance
#         for region, indices in regions.items():
#             region_points = np.array([xy[i] for i in indices])
#             region_center = np.mean(region_points, axis=0)
#             distance = np.linalg.norm(point_coords - region_center)
#
#             if distance < closest_distance:
#                 closest_region = region
#                 closest_distance = distance
#
#         # Add the uncovered point to the closest region
#         regions[closest_region].append(point)
#
#     # Convert regions to a clean dictionary for output
#     return {region: sorted(points) for region, points in regions.items()}
#

def divide_into_regions(xy, num_regions):
    """
    Divide the 2D points `xy` into spatial grid regions using optimized operations.
    Each point is assigned to a region based on its coordinates.

    Parameters:
        xy (np.ndarray): Array of 2D points (shape: Nx2).
        num_regions (int): Number of regions (grid cells).

    Returns:
        dict: Mapping of region indices to point indices in that region.
    """

    # Step 1: Get the bounding box of the dataset
    # x_min, y_min = xy.min(axis=0)
    # x_max, y_max = xy.max(axis=0)

    # Step 2: Determine grid size based on desired number of regions
    grid_size = int(np.ceil(np.sqrt(num_regions)))

    # Step 3: Use adaptive binning for even point distribution (if the dataset is unevenly distributed)
    x_bins = np.histogram_bin_edges(xy[:, 0], bins=grid_size)
    y_bins = np.histogram_bin_edges(xy[:, 1], bins=grid_size)

    # Step 4: Assign points to bins using vectorized logic
    x_bin_indices = np.digitize(xy[:, 0], x_bins) - 1
    y_bin_indices = np.digitize(xy[:, 1], y_bins) - 1

    # Step 5: Combine bin indices into a single unique region index (use integers for better memory/lookup)
    region_indices = x_bin_indices * grid_size + y_bin_indices  # Unique index for each region

    # Step 6: Group points by region
    regions = defaultdict(list)
    for idx, region in enumerate(region_indices):
        regions[region].append(idx)

    # visualize_regions_per_region(xy, regions, x_bins, y_bins)

    plot_convex_hulls(xy, regions, x_bins=x_bins, y_bins=y_bins)

    return regions


def divide_and_conquer_sample_groups(xy, r, k, q,
                                     max_points_per_region=10000,
                                     num_threads=4,
                                     num_candidates_in=50,
                                     min_points_in_region=64000,
                                     # num_threads_candidate_sample=1,
                                     ):
    """
    Divide the points into regions and apply sampling in parallel.

    Parameters:
        xy (np.ndarray): Array of 2D points (shape: Nx2).
        r (float): Minimum radius.
        k (int): Number of points per group.
        q (int): Desired groups per point.
        num_regions (int): Number of spatial regions to split into.
        num_threads (int): Number of parallel worker threads.

    Returns:
        np.ndarray, np.ndarray: All groups and the global coverage array.
    """
    num_regions = int(np.ceil(len(xy) / max_points_per_region))
    logging.info(
        f"Dividing the dataset into {num_regions} regions with {max_points_per_region} points per region."
    )
    # Step 1: Divide the dataset into regions
    # regions = divide_into_regions(xy, num_regions)
    min_pts_in_region = np.min(np.array([min_points_in_region, len(xy)]))
    regions = divide_into_regions_with_min_pts(xy, num_regions, min_pts_in_region)

    ind_list_cnt = list(regions.keys())

    if num_threads > 1:
        # Helper function for processing a single region
        def process_and_update(region_indices):
            try:
                # Sample groups from the given region
                result = sample_groups_region(xy, region_indices, r, k, q,
                                              num_candidates_in, num_threads)
            except Exception as e:
                # Log errors and use empty results to continue processing
                logging.error(f"Error processing region {region_indices}: {e}")
                result = ([], np.zeros(len(xy), dtype=int))  # Fallback empty result
            return result

        # Step 2: Process regions and handle progress updates
        # Create a progress bar to track regions processed
        pbar = tqdm(total=len(regions), desc="Processing regions")

        # Parallel processing, without including tqdm or locks in the workers
        results = []
        for result in Parallel(n_jobs=num_threads)(
                delayed(process_and_update)(region_indices) for region_indices in regions.values()
        ):
            results.append(result)
            pbar.update(1)  # Update progress bar in the main thread!

        # Close the progress bar once processing finishes
        pbar.close()

    else:
        results = []
        for ind in tqdm(ind_list_cnt):
            res_ = sample_groups_region(xy, regions[ind], r, k, q,
                                        num_candidates_in,
                                        num_threads)
            results.append(res_)

    # Step 3: Combine results
    all_groups = []
    global_coverage = np.zeros(len(xy), dtype=int)
    # all_ind = []
    # all_ind_check = []
    for i in range(len(results)):
        for j in range(len(results[i][0])):
            all_groups.append(results[i][0][j])
            # all_ind += list(results[i][0][j])
        global_coverage += results[i][1]

        # print(str(len(regions[ind_list_cnt[i]]) - xy.shape[0]))

        # all_ind_check += regions[ind_list_cnt[i]]

    # all_ind = np.unique(np.array(all_ind))
    # all_ind_check = np.unique(np.array(all_ind_check))
    #

    # for region_groups, regional_coverage in results:
    #     all_groups.extend(region_groups)
    #     global_coverage += regional_coverage

    logging.info(
        f"Number of groups {len(all_groups)} to cover {xy.shape[0]} points."
    )

    return np.array(all_groups), global_coverage
