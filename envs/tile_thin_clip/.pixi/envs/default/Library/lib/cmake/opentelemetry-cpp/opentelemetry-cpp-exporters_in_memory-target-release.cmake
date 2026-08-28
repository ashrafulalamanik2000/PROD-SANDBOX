#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::in_memory_span_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::in_memory_span_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::in_memory_span_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_in_memory.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_in_memory.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::in_memory_span_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::in_memory_span_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_in_memory.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_in_memory.dll" )

# Import target "opentelemetry-cpp::in_memory_metric_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::in_memory_metric_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::in_memory_metric_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_in_memory_metric.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_in_memory_metric.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::in_memory_metric_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::in_memory_metric_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_in_memory_metric.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_in_memory_metric.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
