#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "uriparser::uriparser" for configuration "Release"
set_property(TARGET uriparser::uriparser APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(uriparser::uriparser PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/uriparser.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/uriparser.dll"
  )

list(APPEND _cmake_import_check_targets uriparser::uriparser )
list(APPEND _cmake_import_check_files_for_uriparser::uriparser "${_IMPORT_PREFIX}/lib/uriparser.lib" "${_IMPORT_PREFIX}/bin/uriparser.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
