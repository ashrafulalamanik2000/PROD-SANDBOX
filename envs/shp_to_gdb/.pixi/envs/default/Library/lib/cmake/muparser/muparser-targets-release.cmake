#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "muparser::muparser" for configuration "Release"
set_property(TARGET muparser::muparser APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(muparser::muparser PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/muparser.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/muparser.dll"
  )

list(APPEND _cmake_import_check_targets muparser::muparser )
list(APPEND _cmake_import_check_files_for_muparser::muparser "${_IMPORT_PREFIX}/lib/muparser.lib" "${_IMPORT_PREFIX}/bin/muparser.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
