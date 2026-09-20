"""Realistic content for the MarksWala test fixtures (Data Structures and Algorithms, CS201).

Everything the acceptance test uses — course notes, slides, the question paper and three students'
answers — is authored here so that the expected marks differ meaningfully between students.
"""

COURSE_NAME = "Data Structures and Algorithms"
COURSE_CODE = "CS201"

SYLLABUS = [
    ("Course Objectives", [
        "This course introduces the fundamental data structures and algorithmic techniques used to solve computational "
        "problems efficiently. Students learn to analyse the time and space complexity of algorithms, choose appropriate "
        "data structures, and implement them.",
    ]),
    ("Module 1: Algorithm Analysis", [
        "Asymptotic notation: Big-O, Omega and Theta. Best, average and worst case analysis. Analysing loops and recurrences.",
    ]),
    ("Module 2: Searching and Sorting", [
        "Linear search and binary search. Bubble sort, insertion sort, merge sort and quick sort with their complexities.",
    ]),
    ("Module 3: Stacks and Queues", [
        "The stack ADT (LIFO) and the queue ADT (FIFO). Array and linked implementations. Applications: expression "
        "evaluation, recursion, undo operations, scheduling and breadth first search.",
    ]),
    ("Module 4: Linked Lists", [
        "Singly, doubly and circular linked lists. Insertion, deletion, traversal and reversal.",
    ]),
    ("Module 5: Trees", [
        "Binary trees, binary search trees and tree traversals. Balanced trees (AVL).",
    ]),
    ("Module 6: Graphs", [
        "Graph representations, breadth first search, depth first search, shortest paths and topological ordering.",
    ]),
    ("Module 7: Hashing", [
        "Hash functions, collisions, chaining and open addressing.",
    ]),
    ("Assessment", [
        "Internal Assessment 1 (32 marks) covers Modules 2, 3, 4, 6 and 7. The end semester examination covers the full syllabus.",
    ]),
]

NOTES = [
    ("Module 1: Algorithm Analysis", [
        "The running time of an algorithm is expressed as a function of the input size n. We ignore constant factors and "
        "lower order terms and describe the growth rate using asymptotic notation. Big-O gives an upper bound, Omega gives "
        "a lower bound and Theta gives a tight bound.",
        "A loop that runs n times with constant work per iteration takes O(n) time. Two nested loops that each run n times "
        "take O(n^2). A loop in which the control variable is halved on every iteration runs O(log n) times.",
        "Recurrences describe recursive algorithms. For example T(n) = T(n/2) + O(1) solves to O(log n), while "
        "T(n) = 2T(n/2) + O(n) solves to O(n log n).",
    ]),
    ("Module 2: Searching and Sorting", [
        "Linear search examines the elements one by one until the key is found, so it takes O(n) time in the worst case and "
        "works on unsorted data.",
        "Binary search works only on a sorted array. It compares the key with the middle element. If they are equal the search "
        "ends. If the key is smaller, the search continues in the left half; otherwise it continues in the right half. Each "
        "comparison halves the search space, so after k comparisons at most n / 2^k elements remain.",
        "The recurrence for binary search is T(n) = T(n/2) + O(1). The search stops when the remaining size reaches 1, that "
        "is when n / 2^k = 1, which gives k = log2(n). Therefore the worst case time complexity of binary search is O(log n), "
        "the best case is O(1) when the key is at the middle, and the auxiliary space is O(1) for the iterative version.",
        "Binary search cannot be applied to an unsorted array or to a plain linked list because random access to the middle "
        "element is required and the ordering of the data must be known. Sorting the data first costs O(n log n).",
        "Merge sort divides the array into halves, sorts each half recursively and merges them; it takes O(n log n) time in "
        "every case and O(n) extra space. Quick sort partitions around a pivot; its average time is O(n log n) but the "
        "worst case is O(n^2) when the pivot is always the smallest or largest element.",
    ]),
    ("Module 3: Stacks and Queues", [
        "A stack is a linear data structure in which insertion and deletion take place at one end only, called the top. It "
        "follows the LIFO principle: Last In, First Out. The main operations are push, pop and peek, and each takes O(1) time.",
        "Applications of stacks include the undo mechanism in text editors, the function call stack that supports recursion, "
        "evaluation of postfix expressions, conversion of infix to postfix, matching of balanced parentheses and browser "
        "back navigation.",
        "A queue is a linear data structure in which insertion takes place at the rear and deletion at the front. It follows "
        "the FIFO principle: First In, First Out. The main operations are enqueue and dequeue, each O(1) in a circular array "
        "or linked implementation.",
        "Applications of queues include CPU and disk scheduling, printer spooling, buffering of data streams, and the "
        "breadth first traversal of graphs and trees.",
        "Difference between a stack and a queue: a stack removes the most recently added element (LIFO) using one open end, "
        "whereas a queue removes the earliest added element (FIFO) using two ends. A pile of plates in a cafeteria is a "
        "stack; people standing in a line at a ticket counter form a queue.",
    ]),
    ("Module 4: Linked Lists", [
        "A singly linked list is a sequence of nodes where each node stores data and a pointer to the next node. The list is "
        "accessed through a head pointer. Insertion at the head takes O(1) time, while access by position and search take O(n).",
        "To reverse a singly linked list iteratively we keep three pointers: prev (initially null), curr (initially head) and "
        "next. In each step we save curr.next in next, set curr.next to prev, move prev to curr and curr to next. When curr "
        "becomes null, prev is the new head. The list is traversed once, so the time complexity is O(n) and the extra space "
        "is O(1).",
        "A recursive reversal reverses the rest of the list and then makes the second node point back to the first. It also "
        "takes O(n) time but uses O(n) stack space for the recursion.",
    ]),
    ("Module 5: Trees", [
        "A binary search tree keeps smaller keys in the left subtree and larger keys in the right subtree. Search, insertion "
        "and deletion take O(h) time where h is the height, which is O(log n) for a balanced tree and O(n) for a skewed tree. "
        "In-order traversal of a binary search tree visits the keys in sorted order.",
    ]),
    ("Module 6: Graphs", [
        "A graph G = (V, E) can be stored as an adjacency matrix, which needs O(V^2) space, or as adjacency lists, which need "
        "O(V + E) space.",
        "Breadth first search (BFS) explores the graph level by level starting from a source vertex. It uses a queue. The "
        "algorithm marks the source visited and enqueues it; while the queue is not empty it dequeues a vertex, visits it "
        "and enqueues all its unvisited neighbours after marking them visited. The time complexity is O(V + E) with adjacency "
        "lists. BFS finds the shortest path, in terms of number of edges, in an unweighted graph.",
        "Depth first search (DFS) explores as far as possible along each branch before backtracking. It uses a stack, either "
        "explicitly or through recursion. Its time complexity is also O(V + E). DFS is used for cycle detection, topological "
        "sorting and finding connected components, but it does not guarantee shortest paths.",
        "Comparison of BFS and DFS: BFS uses a queue and DFS uses a stack; BFS visits vertices level by level while DFS goes "
        "deep first; BFS gives shortest paths in unweighted graphs while DFS does not; BFS needs more memory on wide graphs "
        "while DFS needs more on deep graphs. Both run in O(V + E) time.",
    ]),
    ("Module 7: Hashing", [
        "Hashing maps a key to an index of a table using a hash function, giving O(1) average time for search, insertion and "
        "deletion. A good hash function distributes keys uniformly. A collision occurs when two different keys hash to the "
        "same index.",
        "Separate chaining resolves collisions by keeping a linked list of all keys that hash to the same slot. Open "
        "addressing resolves collisions by probing for another free slot in the table itself; linear probing checks the "
        "next slots one by one, quadratic probing uses increasing squared offsets and double hashing uses a second hash "
        "function. The load factor is the ratio of stored keys to table size and controls performance.",
    ]),
]

SLIDES = [
    ("Data Structures and Algorithms", ["CS201 — Lecture Series", "Searching, Stacks, Queues, Graphs and Hashing"]),
    ("Complexity Analysis", ["Big-O: upper bound", "Omega: lower bound", "Theta: tight bound", "Halving loop => O(log n)"]),
    ("Binary Search", ["Requires a SORTED array", "Compare key with the middle element", "Discard half of the search space each step",
                       "T(n) = T(n/2) + O(1) => O(log n)"]),
    ("Stack (LIFO)", ["Insert and delete at the top only", "push, pop, peek: O(1)",
                      "Applications: undo, recursion, expression evaluation, balanced parentheses"]),
    ("Queue (FIFO)", ["Insert at rear, delete at front", "enqueue, dequeue: O(1)",
                      "Applications: scheduling, buffering, breadth first search"]),
    ("Stack versus Queue", ["Stack: last in, first out; one open end", "Queue: first in, first out; two ends",
                            "Example: plates (stack) and a ticket line (queue)"]),
    ("Reversing a Linked List", ["Three pointers: prev, curr, next", "curr.next = prev; then advance all pointers",
                                 "O(n) time, O(1) extra space"]),
    ("Breadth First Search", ["Uses a queue", "Level by level exploration", "Shortest path in unweighted graphs",
                              "O(V + E) with adjacency lists"]),
    ("Depth First Search", ["Uses a stack or recursion", "Explores a branch fully, then backtracks",
                            "Cycle detection, topological sort", "O(V + E)"]),
    ("Hashing and Collisions", ["Hash function maps key -> index", "Chaining: linked list per slot",
                                "Open addressing: linear, quadratic probing, double hashing", "Load factor = n / m"]),
]

# ------------------------------------------------------------------------------------------------------------
# Question paper (32 marks: internal choice between Q4 and Q5 counted once)
# ------------------------------------------------------------------------------------------------------------
PAPER_TITLE = "Internal Assessment 1 — Data Structures and Algorithms (CS201)"
PAPER_HEADER = ["Time: 90 minutes", "Maximum Marks: 32", "Attempt all questions. Q4 and Q5 carry an internal choice: attempt any ONE."]
PAPER = [
    ("SECTION A — Stacks and Queues", [
        ("Q1", None, None),
        ("Q1(a)", "Define a stack and list two of its applications.", 3),
        ("Q1(b)", "Differentiate between a stack and a queue with suitable examples.", 4),
    ]),
    ("SECTION B — Searching and Graphs", [
        ("Q2", "Explain the binary search algorithm. State its precondition and derive its time complexity.", 8),
        ("Q3", None, None),
        ("Q3(a)", "Write the algorithm for breadth first search (BFS) on a graph.", 4),
        ("Q3(b)", "Compare BFS and DFS with respect to the data structure used, time complexity and use cases.", 5),
    ]),
    ("SECTION C — Linked Lists and Hashing (Internal choice: answer Q4 OR Q5)", [
        ("Q4", "Explain how a singly linked list can be reversed. Give the algorithm and state its time and space complexity.", 8),
        ("OR", None, None),
        ("Q5", "Explain hashing. Describe any two collision resolution techniques.", 8),
    ]),
]
EXPECTED_TOTAL = 32

# ------------------------------------------------------------------------------------------------------------
# Student answers. Each entry: (label as the student wrote it, text). Order = order on the sheet.
# "\n" separates lines; "[[DIAGRAM:stack]]" draws a stack diagram.
# ------------------------------------------------------------------------------------------------------------
STUDENTS = {
    "A": {
        "name": "Aarav Sharma", "roll": "CS2024-001", "font": "IndieFlower-Regular.ttf",
        "answers": [
            ("Q1(a)", "A stack is a linear data structure in which insertion and deletion are done at one end only, called the "
                      "top. It follows the LIFO principle - Last In First Out.\n"
                      "[[DIAGRAM:stack]]\n"
                      "Applications: 1) undo operation in text editors  2) function call stack used in recursion."),
            ("Q1(b)", "Stack follows LIFO whereas a queue follows FIFO (First In First Out). In a stack insertion and deletion happen "
                      "at the same end (top) but in a queue insertion is at the rear and deletion is at the front.\n"
                      "Example: a pile of plates in a cafeteria is a stack, people waiting in a line at a ticket counter form a queue."),
            ("Q2", "Binary search is used to search a key in a SORTED array. Precondition: the array must be sorted.\n"
                   "Algorithm: find the middle element. If key == mid, return. If key < mid search the left half, else search the "
                   "right half. Repeat until found or the range is empty.\n"
                   "Complexity: each step halves the search space, so after k steps n/2^k elements are left. Search ends when "
                   "n/2^k = 1, so k = log2 n. The recurrence is T(n) = T(n/2) + O(1) which gives O(log n) in the worst case. "
                   "Best case is O(1). Space is O(1) for the iterative version."),
            ("Q3(a)", "BFS algorithm:\n1. Mark the source vertex visited and enqueue it.\n2. While the queue is not empty: dequeue a vertex u "
                      "and visit it.\n3. For every unvisited neighbour v of u, mark v visited and enqueue it.\n"
                      "Time complexity is O(V + E) using adjacency lists."),
            ("Q3(b)", "BFS uses a queue and explores level by level; DFS uses a stack (or recursion) and goes deep along a branch before "
                      "backtracking.\nBoth take O(V + E) time.\nUse cases: BFS gives shortest path in an unweighted graph; DFS is used for "
                      "cycle detection, topological sort and connected components."),
            ("Q4", "To reverse a singly linked list we use three pointers prev, curr and next. Initially prev = null and curr = head.\n"
                   "Loop while curr is not null: next = curr.next ; curr.next = prev ; prev = curr ; curr = next.\n"
                   "At the end prev is the new head.\nThe list is traversed once so the time complexity is O(n) and the extra space is O(1)."),
        ],
    },
    "B": {
        "name": "Bhavna Iyer", "roll": "CS2024-002", "font": "ShadowsIntoLight.ttf",
        # answers Q3 before Q2 (out of order); Q2 lacks the sorted precondition and derivation; Q3(b) is thin
        "answers": [
            ("Q1(a)", "Stack is a data structure that works on LIFO order, the last element inserted is removed first. "
                      "It is used in undo operation of an editor and also to check balanced brackets."),
            ("Q1(b)", "Stack - LIFO, queue - FIFO. In stack we push and pop from the top. In queue elements are added at rear and "
                      "removed from front. Stack example is books piled up, queue example is a queue at a bank."),
            ("Q3(a)", "BFS: start from source, put it in a queue. Take a node out from the queue, and add all its neighbours which are "
                      "not visited to the queue. Repeat till queue becomes empty."),
            ("Q3(b)", "BFS uses queue and DFS uses stack. BFS goes level wise and DFS goes deep first."),
            ("Q2", "Binary search is a searching method in which we find the middle of the array and compare the key with it. If the key "
                   "is smaller we go to the left part otherwise the right part. This is repeated. Because we keep dividing the array "
                   "into two parts the complexity is O(log n)."),
            ("Q4", "For reversing a linked list we change the direction of the pointers so that each node points to the previous node "
                   "instead of the next node. We take pointers prev and curr and move through the list. The time taken is O(n)."),
        ],
    },
    "C": {
        "name": "Chirag Patel", "roll": "CS2024-003", "font": "GochiHand-Regular.ttf",
        # wrong definition, confuses binary with linear search, skips Q3(b) and Q4, attempts Q5 vaguely
        "answers": [
            ("Q1(a)", "A stack is a data structure which works in first in first out manner. It is used to store data in a program."),
            ("Q1(b)", "Stack and queue are both linear data structures. Stack is used more than queue."),
            ("Q2", "Binary search is a method to find an element. We check each element of the array one by one from the start "
                   "till we get the element. Its complexity is O(n)."),
            ("Q3(a)", "BFS means we visit the nodes of a graph. We start from a node and go to its neighbour nodes."),
            ("Q5", "Hashing is used to store data in a table. There can be collision when two keys go to same place. "
                   "It can be solved by chaining."),
        ],
    },
}
