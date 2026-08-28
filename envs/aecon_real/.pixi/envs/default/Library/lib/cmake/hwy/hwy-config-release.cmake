#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "hwy::hwy" for configuration "Release"
set_property(TARGET hwy::hwy APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(hwy::hwy PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/hwy.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/hwy.dll"
  )

list(APPEND _cmake_import_check_targets hwy::hwy )
list(APPEND _cmake_import_check_files_for_hwy::hwy "${_IMPORT_PREFIX}/lib/hwy.lib" "${_IMPORT_PREFIX}/bin/hwy.dll" )

# Import target "hwy::hwy_contrib" for configuration "Release"
set_property(TARGET hwy::hwy_contrib APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(hwy::hwy_contrib PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/hwy_contrib.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/hwy_contrib.dll"
  )

list(APPEND _cmake_import_check_targets hwy::hwy_contrib )
list(APPEND _cmake_import_check_files_for_hwy::hwy_contrib "${_IMPORT_PREFIX}/lib/hwy_contrib.lib" "${_IMPORT_PREFIX}/bin/hwy_contrib.dll" )

# Import target "hwy::hwy_test" for configuration "Release"
set_property(TARGET hwy::hwy_test APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(hwy::hwy_test PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/hwy_test.lib"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/hwy_test.dll"
  )

list(APPEND _cmake_import_check_targets hwy::hwy_test )
list(APPEND _cmake_import_check_files_for_hwy::hwy_test "${_IMPORT_PREFIX}/lib/hwy_test.lib" "${_IMPORT_PREFIX}/bin/hwy_test.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
