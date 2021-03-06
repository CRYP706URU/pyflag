/*
** dcalc
** The Sleuth Kit 
**
** $Date: 2007/12/20 20:32:38 $
**
** Calculates the corresponding block number between 'dls' and 'dd' images
** when given an 'dls' block number, it determines the block number it
** had in a 'dd' image.  When given a 'dd' image, it determines the
** value it would have in a 'dls' image (if the block is unallocated)
**
** Brian Carrier [carrier@sleuthkit.org]
** Copyright (c) 2006-2007 Brian Carrier, Basis Technology.  All Rights reserved
** Copyright (c) 2003-2005 Brian Carrier. All Rights reserved
**
** TASK
** Copyright (c) 2002 Brian Carrier, @stake Inc. All Rights reserved
**
** TCTUTILs
** Copyright (c) 2001 Brian Carrier.  All rights reserved
**
**
** This software is distributed under the Common Public License 1.0
**
*/

/**
 * \file dcalc_lib.c
 * Contains the library API functions used by the dcalc command
 * line tool.
 */

#include "tsk_fs_i.h"


static TSK_DADDR_T count;
static TSK_DADDR_T uncnt = 0;

static uint8_t found = 0;



/* function used when -d is given
**
** keeps a count of unallocated blocks seen thus far
**
** If the specified block is allocated, an error is given, else the
** count of unalloc blocks is given 
**
** This is called for all blocks (alloc and unalloc)
*/
static TSK_WALK_RET_ENUM
count_dd_act(TSK_FS_INFO * fs, TSK_DADDR_T addr, char *buf,
    TSK_FS_BLOCK_FLAG_ENUM flags, void *ptr)
{
    if (flags & TSK_FS_BLOCK_FLAG_UNALLOC)
        uncnt++;

    if (count-- == 0) {
        if (flags & TSK_FS_BLOCK_FLAG_UNALLOC)
            tsk_printf("%" PRIuDADDR "\n", uncnt);
        else
            printf
                ("ERROR: unit is allocated, it will not be in an dls image\n");

        found = 1;
        return TSK_WALK_STOP;
    }
    return TSK_WALK_CONT;
}

/*
** count how many unalloc blocks there are.
**
** This is called for unalloc blocks only
*/
static TSK_WALK_RET_ENUM
count_dls_act(TSK_FS_INFO * fs, TSK_DADDR_T addr, char *buf,
    TSK_FS_BLOCK_FLAG_ENUM flags, void *ptr)
{
    if (count-- == 0) {
        tsk_printf("%" PRIuDADDR "\n", addr);
        found = 1;
        return TSK_WALK_STOP;
    }
    return TSK_WALK_CONT;
}


/* SLACK SPACE  call backs */
static TSK_OFF_T flen;

static TSK_WALK_RET_ENUM
count_slack_file_act(TSK_FS_INFO * fs, TSK_DADDR_T addr, char *buf,
    size_t size, TSK_FS_BLOCK_FLAG_ENUM flags, void *ptr)
{

    if (tsk_verbose)
        tsk_fprintf(stderr,
            "count_slack_file_act: Remaining File:  %"PRIuOFF"  Buffer: %zu\n",
            flen, size);

    /* This is not the last data unit */
    if (flen >= size) {
        flen -= size;
    }
    /* We have passed the end of the allocated space */
    else if (flen == 0) {
        if (count-- == 0) {
            tsk_printf("%" PRIuDADDR "\n", addr);
            found = 1;
            return TSK_WALK_STOP;

        }
    }
    /* This is the last data unit and there is unused space */
    else if (flen < size) {
        if (count-- == 0) {
            tsk_printf("%" PRIuDADDR "\n", addr);
            found = 1;
            return TSK_WALK_STOP;

        }
        flen = 0;
    }

    return TSK_WALK_CONT;
}

static TSK_WALK_RET_ENUM
count_slack_inode_act(TSK_FS_INFO * fs, TSK_FS_INODE * fs_inode, void *ptr)
{
    if (tsk_verbose)
        tsk_fprintf(stderr,
            "count_slack_inode_act: Processing meta data: %" PRIuINUM
            "\n", fs_inode->addr);

    /* We will now do a file walk on the content */
    if ((fs->ftype & TSK_FS_INFO_TYPE_FS_MASK) !=
        TSK_FS_INFO_TYPE_NTFS_TYPE) {
        flen = fs_inode->size;
        if (fs->file_walk(fs, fs_inode, 0, 0,
                TSK_FS_FILE_FLAG_SLACK |
                TSK_FS_FILE_FLAG_NOID, count_slack_file_act, ptr)) {

            /* ignore any errors */
            if (tsk_verbose)
                tsk_fprintf(stderr, "Error walking file %" PRIuINUM,
                    fs_inode->addr);
            tsk_error_reset();
        }
    }

    /* For NTFS we go through each non-resident attribute */
    else {
        TSK_FS_DATA *fs_data;

        for (fs_data = fs_inode->attr;
            fs_data != NULL; fs_data = fs_data->next) {

            if ((fs_data->flags & TSK_FS_DATA_INUSE) == 0)
                continue;

            if (fs_data->flags & TSK_FS_DATA_NONRES) {
                flen = fs_data->size;
                if (fs->file_walk(fs, fs_inode, fs_data->type, fs_data->id,
                        TSK_FS_FILE_FLAG_SLACK, count_slack_file_act,
                        ptr)) {
                    /* ignore any errors */
                    if (tsk_verbose)
                        tsk_fprintf(stderr,
                            "Error walking file %" PRIuINUM,
                            fs_inode->addr);
                    tsk_error_reset();
                }
            }
        }
    }
    return TSK_WALK_CONT;
}




/* Return 1 if block is not found, 0 if it was found, and -1 on error */
int8_t
tsk_fs_dcalc(TSK_FS_INFO * fs, uint8_t lclflags, TSK_DADDR_T cnt)
{
    count = cnt;

    found = 0;

    if (lclflags == TSK_FS_DCALC_DLS) {
        if (fs->block_walk(fs, fs->first_block, fs->last_block,
                (TSK_FS_BLOCK_FLAG_UNALLOC | TSK_FS_BLOCK_FLAG_ALIGN |
                    TSK_FS_BLOCK_FLAG_META | TSK_FS_BLOCK_FLAG_CONT),
                count_dls_act, NULL))
            return -1;
    }
    else if (lclflags == TSK_FS_DCALC_DD) {
        if (fs->block_walk(fs, fs->first_block, fs->last_block,
                (TSK_FS_BLOCK_FLAG_ALLOC | TSK_FS_BLOCK_FLAG_UNALLOC |
                    TSK_FS_BLOCK_FLAG_ALIGN | TSK_FS_BLOCK_FLAG_META |
                    TSK_FS_BLOCK_FLAG_CONT), count_dd_act, NULL))
            return -1;
    }
    else if (lclflags == TSK_FS_DCALC_SLACK) {
        if (fs->inode_walk(fs, fs->first_inum, fs->last_inum,
                TSK_FS_INODE_FLAG_ALLOC, count_slack_inode_act, NULL))
            return -1;
    }

    if (found == 0) {
        tsk_printf("Block too large\n");
        return 1;
    }
    else {
        return 0;
    }
}
