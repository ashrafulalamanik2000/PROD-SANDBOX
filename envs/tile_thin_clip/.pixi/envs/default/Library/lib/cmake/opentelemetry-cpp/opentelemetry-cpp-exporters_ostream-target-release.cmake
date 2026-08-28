#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::ostream_span_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::ostream_span_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::ostream_span_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_ostream_span.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_ostream_span.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::ostream_span_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::ostream_span_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_ostream_span.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_ostream_span.dll" )

# Import target "opentelemetry-cpp::ostream_metrics_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::ostream_metrics_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::ostream_metrics_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_ostream_metrics.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_ostream_metrics.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::ostream_metrics_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::ostream_metrics_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_ostream_metrics.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_ostream_metrics.dll" )

# Import target "opentelemetry-cpp::ostream_log_record_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::ostream_log_record_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::ostream_log_record_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_ostream_logs.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_ostream_logs.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::ostream_log_record_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::ostream_log_record_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_ostream_logs.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_ostream_logs.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
