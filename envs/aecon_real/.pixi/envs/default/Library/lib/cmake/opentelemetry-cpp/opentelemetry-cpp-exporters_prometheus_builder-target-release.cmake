#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::prometheus_exporter_builder" for configuration "Release"
set_property(TARGET opentelemetry-cpp::prometheus_exporter_builder APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::prometheus_exporter_builder PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_prometheus_builder.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_prometheus_builder.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::prometheus_exporter_builder )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::prometheus_exporter_builder "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_prometheus_builder.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_prometheus_builder.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
