#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "thriftnb::thriftnb" for configuration "Release"
set_property(TARGET thriftnb::thriftnb APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(thriftnb::thriftnb PROPERTIES
  IMPORTED_IMPLIB_RELEASE "C:/sdtools/envs/aecon_real/.pixi/envs/default/Library/lib/thriftnbmd.lib"
  IMPORTED_LOCATION_RELEASE "C:/sdtools/envs/aecon_real/.pixi/envs/default/Library/bin/thriftnbmd.dll"
  )

list(APPEND _cmake_import_check_targets thriftnb::thriftnb )
list(APPEND _cmake_import_check_files_for_thriftnb::thriftnb "C:/sdtools/envs/aecon_real/.pixi/envs/default/Library/lib/thriftnbmd.lib" "C:/sdtools/envs/aecon_real/.pixi/envs/default/Library/bin/thriftnbmd.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
