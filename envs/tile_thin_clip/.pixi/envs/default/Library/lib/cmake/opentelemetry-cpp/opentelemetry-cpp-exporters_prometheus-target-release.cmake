#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::prometheus_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::prometheus_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::prometheus_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_prometheus.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "prometheus-cpp::pull"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_prometheus.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::prometheus_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::prometheus_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_prometheus.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_prometheus.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
