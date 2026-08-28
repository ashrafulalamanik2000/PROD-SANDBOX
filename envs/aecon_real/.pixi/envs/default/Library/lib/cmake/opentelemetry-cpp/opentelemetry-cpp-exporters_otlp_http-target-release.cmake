#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::otlp_http_client" for configuration "Release"
set_property(TARGET opentelemetry-cpp::otlp_http_client APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::otlp_http_client PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_http_client.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "opentelemetry-cpp::http_client_curl"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_http_client.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::otlp_http_client )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::otlp_http_client "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_http_client.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_http_client.dll" )

# Import target "opentelemetry-cpp::otlp_http_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::otlp_http_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::otlp_http_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_http.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_http.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::otlp_http_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::otlp_http_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_http.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_http.dll" )

# Import target "opentelemetry-cpp::otlp_http_log_record_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::otlp_http_log_record_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::otlp_http_log_record_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_http_log.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_http_log.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::otlp_http_log_record_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::otlp_http_log_record_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_http_log.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_http_log.dll" )

# Import target "opentelemetry-cpp::otlp_http_metric_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::otlp_http_metric_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::otlp_http_metric_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_http_metric.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_http_metric.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::otlp_http_metric_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::otlp_http_metric_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_http_metric.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_http_metric.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
