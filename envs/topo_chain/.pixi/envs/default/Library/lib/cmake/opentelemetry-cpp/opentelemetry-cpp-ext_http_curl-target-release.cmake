#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "opentelemetry-cpp::http_client_curl" for configuration "Release"
set_property(TARGET opentelemetry-cpp::http_client_curl APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(opentelemetry-cpp::http_client_curl PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/opentelemetry_http_client_curl.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/opentelemetry_http_client_curl.dll"
  )

list(APPEND _cmake_import_check_targets opentelemetry-cpp::http_client_curl )
list(APPEND _cmake_import_check_files_for_opentelemetry-cpp::http_client_curl "${_IMPORT_PREFIX}/lib/opentelemetry_http_client_curl.lib" "${_IMPORT_PREFIX}/bin/opentelemetry_http_client_curl.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
