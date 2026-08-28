if ($ENV:PDAL_DRIVER_PATH) {
    $ENV:_CONDA_SET_PDAL_DRIVER_PATH = $ENV:PDAL_DRIVER_PATH
}

$pdal_paths = @()
if ($ENV:PDAL_DRIVER_PATH) {
    $pdal_paths += $ENV:PDAL_DRIVER_PATH
}
$pdal_paths += "$ENV:CONDA_PREFIX\Lib\site-packages\bin"
$pdal_paths += "$ENV:CONDA_PREFIX\bin"

$ENV:PDAL_DRIVER_PATH = $pdal_paths -join ";"