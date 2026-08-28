#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::zipkin_trace_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::zipkin_trace_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::zipkin_trace_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_zipkin_trace.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_zipkin_trace.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::zipkin_trace_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::zipkin_trace_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_zipkin_trace.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_zipkin_trace.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
