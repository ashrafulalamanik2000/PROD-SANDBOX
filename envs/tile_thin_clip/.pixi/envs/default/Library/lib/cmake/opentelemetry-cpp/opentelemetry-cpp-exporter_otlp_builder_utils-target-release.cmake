#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::otlp_builder_utils" for configuration "Release"
set_property(TARGET opentelemetry-cpp::otlp_builder_utils APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::otlp_builder_utils PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_builder_utils.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_builder_utils.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::otlp_builder_utils )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::otlp_builder_utils "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_builder_utils.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_builder_utils.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
