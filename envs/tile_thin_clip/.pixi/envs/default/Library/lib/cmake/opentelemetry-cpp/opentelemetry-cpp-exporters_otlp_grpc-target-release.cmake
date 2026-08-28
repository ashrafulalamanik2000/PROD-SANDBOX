#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::otlp_grpc_client" for configuration "Release"
set_property(TARGET opentelemetry-cpp::otlp_grpc_client APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::otlp_grpc_client PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_grpc_client.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "gRPC::grpc++"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_grpc_client.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::otlp_grpc_client )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::otlp_grpc_client "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_grpc_client.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_grpc_client.dll" )

# Import target "opentelemetry-cpp::proto_grpc" for configuration "Release"
set_property(TARGET opentelemetry-cpp::proto_grpc APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::proto_grpc PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_proto_grpc.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_proto_grpc.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::proto_grpc )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::proto_grpc "${_IMPORT_PREFIX}/lib/opentelemetry_proto_grpc.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_proto_grpc.dll" )

# Import target "opentelemetry-cpp::otlp_grpc_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::otlp_grpc_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::otlp_grpc_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_grpc.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "gRPC::grpc++"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_grpc.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::otlp_grpc_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::otlp_grpc_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_grpc.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_grpc.dll" )

# Import target "opentelemetry-cpp::otlp_grpc_log_record_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::otlp_grpc_log_record_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::otlp_grpc_log_record_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_grpc_log.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "gRPC::grpc++"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_grpc_log.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::otlp_grpc_log_record_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::otlp_grpc_log_record_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_grpc_log.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_grpc_log.dll" )

# Import target "opentelemetry-cpp::otlp_grpc_metrics_exporter" for configuration "Release"
set_property(TARGET opentelemetry-cpp::otlp_grpc_metrics_exporter APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::otlp_grpc_metrics_exporter PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_grpc_metrics.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "gRPC::grpc++"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_grpc_metrics.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::otlp_grpc_metrics_exporter )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::otlp_grpc_metrics_exporter "${_IMPORT_PREFIX}/lib/opentelemetry_exporter_otlp_grpc_metrics.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_exporter_otlp_grpc_metrics.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
