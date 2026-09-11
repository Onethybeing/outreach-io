"use client"

import {
  type ColumnDef,
  type RowData,
  type RowSelectionState,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table"
import { ArrowDownIcon, ArrowUpDownIcon, ArrowUpIcon, SearchIcon } from "lucide-react"
import { useMemo, useState } from "react"

import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

declare module "@tanstack/react-table" {
  // Type parameters must match the library's declaration for the merge to apply.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    className?: string
  }
}

const NO_ROWS: never[] = []

type DataTableProps<T> = {
  columns: ColumnDef<T>[]
  data: T[] | undefined
  getRowId: (row: T) => string
  loading?: boolean
  error?: string
  empty?: string
  /** Placeholder for the search box; omit to hide it. */
  search?: string
  rowSelection?: RowSelectionState
  /** Passing this turns on the checkbox column. */
  onRowSelectionChange?: (next: RowSelectionState) => void
  toolbar?: React.ReactNode
}

export function DataTable<T>({
  columns, data, getRowId, loading, error, empty = "Nothing here yet.", search, rowSelection, onRowSelectionChange, toolbar,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState("")
  const selection = rowSelection ?? {}
  const selectable = onRowSelectionChange !== undefined

  const allColumns = useMemo<ColumnDef<T>[]>(() => {
    if (!selectable) return columns
    const select: ColumnDef<T> = {
      id: "_select",
      enableSorting: false,
      meta: { className: "w-8" },
      header: ({ table }) => {
        // Only the rows on screen (after search), so hidden rows never get swept into a bulk action.
        const rows = table.getRowModel().rows
        const selected = rows.filter((row) => row.getIsSelected()).length
        return (
          <Checkbox
            aria-label="Select all"
            checked={rows.length > 0 && selected === rows.length ? true : selected > 0 ? "indeterminate" : false}
            onCheckedChange={(value) =>
              table.setRowSelection((prev) => {
                const next = { ...prev }
                for (const row of rows) {
                  if (value === true) next[row.id] = true
                  else delete next[row.id]
                }
                return next
              })
            }
          />
        )
      },
      cell: ({ row }) => (
        <Checkbox aria-label="Select row" checked={row.getIsSelected()} onCheckedChange={(value) => row.toggleSelected(value === true)} />
      ),
    }
    return [select, ...columns]
  }, [columns, selectable])

  const table = useReactTable({
    data: data ?? NO_ROWS,
    columns: allColumns,
    getRowId,
    state: { sorting, globalFilter, rowSelection: selection },
    enableRowSelection: selectable,
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onRowSelectionChange: (updater) => onRowSelectionChange?.(typeof updater === "function" ? updater(selection) : updater),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  })

  const rows = table.getRowModel().rows

  return (
    <div className="space-y-3">
      {(search || toolbar) && (
        <div className="flex flex-wrap items-center gap-2">
          {search && (
            <div className="relative">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={globalFilter} onChange={(e) => setGlobalFilter(e.target.value)} placeholder={search} className="h-8 w-64 pl-8" />
            </div>
          )}
          {toolbar}
        </div>
      )}
      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {group.headers.map((header) => {
                  const label = header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())
                  const sorted = header.column.getIsSorted()
                  return (
                    <TableHead key={header.id} className={header.column.columnDef.meta?.className}>
                      {header.column.getCanSort() ? (
                        <button type="button" className="inline-flex items-center gap-1 hover:text-foreground" onClick={header.column.getToggleSortingHandler()}>
                          {label}
                          {sorted === "asc" ? <ArrowUpIcon className="size-3" /> : sorted === "desc" ? <ArrowDownIcon className="size-3" /> : <ArrowUpDownIcon className="size-3 opacity-40" />}
                        </button>
                      ) : (
                        label
                      )}
                    </TableHead>
                  )
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {rows.length > 0 ? (
              rows.map((row) => (
                <TableRow key={row.id} data-state={row.getIsSelected() ? "selected" : undefined}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className={cell.column.columnDef.meta?.className}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={allColumns.length} className="h-24 text-center whitespace-normal text-muted-foreground">
                  {data === undefined && loading ? (
                    <div className="space-y-2">
                      {[0, 1, 2].map((i) => (
                        <Skeleton key={i} className="h-5 w-full" />
                      ))}
                    </div>
                  ) : data === undefined && error ? (
                    <span className="text-destructive">{error}</span>
                  ) : (
                    empty
                  )}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

export function selectedIds<T extends { id: string }>(rows: T[] | undefined, selection: RowSelectionState): string[] {
  // Intersect with the rows currently loaded: a selected row may have moved to another view since.
  return (rows ?? []).filter((row) => selection[row.id]).map((row) => row.id)
}
