#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::common" for configuration "Release"
set_property(TARGET opentelemetry-cpp::common APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::common PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_common.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_common.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::common )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::common "${_IMPORT_PREFIX}/lib/opentelemetry_common.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_common.dll" )

# Import target "opentelemetry-cpp::resources" for configuration "Release"
set_property(TARGET opentelemetry-cpp::resources APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::resources PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_resources.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_resources.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::resources )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::resources "${_IMPORT_PREFIX}/lib/opentelemetry_resources.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_resources.dll" )

# Import target "opentelemetry-cpp::version" for configuration "Release"
set_property(TARGET opentelemetry-cpp::version APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::version PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_version.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_version.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::version )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::version "${_IMPORT_PREFIX}/lib/opentelemetry_version.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_version.dll" )

# Import target "opentelemetry-cpp::logs" for configuration "Release"
set_property(TARGET opentelemetry-cpp::logs APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::logs PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_logs.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_logs.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::logs )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::logs "${_IMPORT_PREFIX}/lib/opentelemetry_logs.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_logs.dll" )

# Import target "opentelemetry-cpp::trace" for configuration "Release"
set_property(TARGET opentelemetry-cpp::trace APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::trace PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_trace.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_trace.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::trace )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::trace "${_IMPORT_PREFIX}/lib/opentelemetry_trace.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_trace.dll" )

# Import target "opentelemetry-cpp::metrics" for configuration "Release"
set_property(TARGET opentelemetry-cpp::metrics APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::metrics PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_metrics.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_metrics.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::metrics )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::metrics "${_IMPORT_PREFIX}/lib/opentelemetry_metrics.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_metrics.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
